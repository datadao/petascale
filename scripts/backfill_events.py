"""Backfill detected events over a historical raw_measurements window.

Usage:
    uv run python scripts/backfill_events.py \\
        --db /data/petascale/petascale.db \\
        --sensor sensor14/cat_weight_sensor \\
        --since 2026-04-01 \\
        --until 2026-05-02

Iterates day-chunked to keep memory bounded. Idempotent — re-running is safe
(events keyed on (sensor_id, timestamp, type) with ON CONFLICT DO NOTHING).
"""

import argparse
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta

from petascale.config import DetectionConfig, load_config
from petascale.warm.litterbox import process_sensor

log = logging.getLogger(__name__)


def _day_to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to petascale.db")
    parser.add_argument("--sensor", required=True, help="Sensor id (matches config)")
    parser.add_argument("--since", required=True, help="Inclusive start date (YYYY-MM-DD, UTC)")
    parser.add_argument("--until", required=True, help="Exclusive end date (YYYY-MM-DD, UTC)")
    parser.add_argument("--role", default="litterbox", help="Detection params role")
    parser.add_argument("--chunk-days", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    det_cfg = cfg.detection.get(args.role, DetectionConfig())
    cats = cfg.cats

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    if until <= since:
        raise SystemExit("--until must be after --since")

    conn = sqlite3.connect(args.db, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Ensure the events table exists — useful when backfilling against a DB
    # that's only seen the ingest daemon's older schema.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            timestamp        INTEGER NOT NULL,
            type             TEXT    NOT NULL,
            sensor_id        TEXT    NOT NULL,
            cat              TEXT,
            weight_g         INTEGER,
            cat_distance_g   INTEGER,
            segment_start_ts INTEGER NOT NULL,
            segment_end_ts   INTEGER NOT NULL,
            created_at       INTEGER DEFAULT (strftime('%s', 'now') * 1000),
            PRIMARY KEY (sensor_id, timestamp, type)
        )
    """)
    conn.commit()

    total = 0
    chunk = timedelta(days=args.chunk_days)
    cur = since
    try:
        while cur < until:
            nxt = min(cur + chunk, until)
            n = process_sensor(
                conn, args.sensor, det_cfg, cats,
                since_ms=_day_to_ms(cur), until_ms=_day_to_ms(nxt),
            )
            log.info("backfilled %s → %s: %d events", cur, nxt, n)
            total += n
            cur = nxt
    finally:
        conn.close()

    log.info("done — %d events", total)


if __name__ == "__main__":
    main()
