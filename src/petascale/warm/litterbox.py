"""Warm-path detector for litterbox sensors.

Polls SQLite every `warm_interval_seconds` for the most recent
`warm_window_minutes` of raw readings per litterbox sensor, runs the
detection pipeline, and upserts events. Idempotent: events are keyed by
(sensor_id, timestamp_ms, type) so re-running over the same window is safe.
"""

import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import structlog
from dotenv import load_dotenv

from petascale.config import AppConfig, CatProfile, DetectionConfig, load_config
from petascale.detect import DetectorEvent
from petascale.detect import run as run_pipeline

logger = structlog.get_logger()


def fetch_window(
    conn: sqlite3.Connection,
    sensor_id: str,
    since_ms: int,
    until_ms: int,
) -> pl.DataFrame:
    """Read raw readings as a polars DataFrame with (ts, value) columns."""
    rows = conn.execute(
        """
        SELECT timestamp, value FROM raw_measurements
        WHERE sensor_id = ? AND timestamp >= ? AND timestamp < ?
        ORDER BY timestamp
        """,
        (sensor_id, since_ms, until_ms),
    ).fetchall()
    if not rows:
        return pl.DataFrame(
            {"ts": [], "value": []},
            schema={"ts": pl.Datetime("us"), "value": pl.Float64},
        )
    return pl.DataFrame(
        {
            "ts": [datetime.fromtimestamp(r[0] / 1000.0, tz=UTC).replace(tzinfo=None)
                   for r in rows],
            "value": [float(r[1]) for r in rows],
        },
        schema={"ts": pl.Datetime("us"), "value": pl.Float64},
    )


def upsert_events(
    conn: sqlite3.Connection,
    sensor_id: str,
    events: list[DetectorEvent],
) -> int:
    """Insert events idempotently. Returns count attempted."""
    if not events:
        return 0
    rows = [
        (
            int(e.timestamp.replace(tzinfo=UTC).timestamp() * 1000),
            e.type,
            sensor_id,
            e.cat,
            e.weight_g,
            e.cat_distance_g,
            int(e.segment_start.replace(tzinfo=UTC).timestamp() * 1000),
            int(e.segment_end.replace(tzinfo=UTC).timestamp() * 1000),
        )
        for e in events
    ]
    conn.executemany(
        """
        INSERT INTO events
            (timestamp, type, sensor_id, cat, weight_g, cat_distance_g,
             segment_start_ts, segment_end_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (sensor_id, timestamp, type) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def process_sensor(
    conn: sqlite3.Connection,
    sensor_id: str,
    cfg: DetectionConfig,
    cats: list[CatProfile],
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    live: bool = False,
) -> int:
    """Run the pipeline for one sensor over a window. Returns events upserted.

    When live=True (warm daemon mode), segments whose end touches the window
    boundary are suppressed. This prevents the daemon from minting a new event
    timestamp every 60 s for a visit that is still in progress: segment_end is
    times.item(-1) in the segment DataFrame, so it advances with the window
    until the cat leaves and the segment closes.
    """
    until = until_ms if until_ms is not None else int(time.time() * 1000)
    since = since_ms if since_ms is not None else until - cfg.warm_window_minutes * 60_000

    df = fetch_window(conn, sensor_id, since, until)
    if df.is_empty():
        return 0
    events = run_pipeline(df, cfg, cats)
    if not events:
        return 0

    if live:
        until_dt = datetime.utcfromtimestamp(until / 1000.0)
        events = [
            e for e in events
            if (until_dt - e.segment_end).total_seconds() > 2
        ]
        if not events:
            return 0

    upsert_events(conn, sensor_id, events)
    logger.info(
        "Detected events",
        sensor_id=sensor_id,
        n=len(events),
        window_minutes=(until - since) // 60_000,
    )
    return len(events)


class WarmDaemon:
    """Periodic warm-path detector across all litterbox sensors."""

    def __init__(self) -> None:
        load_dotenv()
        self.db_path: str = os.getenv("DB_PATH", "petascale.db")
        self.cfg: AppConfig = load_config()
        self._shutdown = False

    def run(self) -> None:
        litterboxes = [s for s in self.cfg.sensors if s.role == "litterbox"]
        if not litterboxes:
            logger.warning("No litterbox sensors configured, exiting")
            return
        det_cfg = self.cfg.detection.get("litterbox", DetectionConfig())
        cats = self.cfg.cats

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            logger.info(
                "Warm daemon started",
                sensors=len(litterboxes),
                cats=len(cats),
                interval_s=det_cfg.warm_interval_seconds,
                window_min=det_cfg.warm_window_minutes,
            )
            while not self._shutdown:
                for s in litterboxes:
                    try:
                        process_sensor(conn, s.id, det_cfg, cats, live=True)
                    except Exception:
                        logger.exception("Warm tick failed", sensor_id=s.id)
                self._sleep(det_cfg.warm_interval_seconds)
        finally:
            conn.close()
            logger.info("Warm daemon stopped")

    def _sleep(self, seconds: int) -> None:
        # Sleep in small steps so SIGTERM unblocks promptly.
        end = time.time() + seconds
        while not self._shutdown and time.time() < end:
            time.sleep(0.5)

    def request_shutdown(self, *_: object) -> None:
        logger.info("Shutdown requested")
        self._shutdown = True


def main() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO), stream=sys.stderr)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    daemon = WarmDaemon()
    signal.signal(signal.SIGINT, daemon.request_shutdown)
    signal.signal(signal.SIGTERM, daemon.request_shutdown)
    daemon.run()


if __name__ == "__main__":
    main()
