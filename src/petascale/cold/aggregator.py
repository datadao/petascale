"""Nightly cold-path aggregator: exports raw_measurements to daily Parquet files.

Run manually:
    uv run --extra analytics python -m petascale.cold.aggregator --db /data/petascale.db

Or via docker-compose analytics service (runs in a loop, exports each night).
"""

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

_ARCHIVE_DIR = Path("/data/archive")
_DB_DEFAULT = "/data/petascale.db"


def export_day(con: duckdb.DuckDBPyConnection, day: date, out_dir: Path) -> int:
    """Export one calendar day to a Parquet file. Returns row count.

    Writes to a .tmp file first, then renames atomically — safe against
    partial writes and won't destroy an existing file if there's no data.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{day}.parquet"
    tmp = out_dir / f"{day}.parquet.tmp"

    start_ms = int(date.fromisoformat(str(day)).strftime("%s")) * 1000
    end_ms = start_ms + 86_400_000

    count = con.execute(
        "SELECT count(*) FROM sqlite.raw_measurements WHERE timestamp >= ? AND timestamp < ?",
        [start_ms, end_ms],
    ).fetchone()[0]

    if count == 0:
        return 0

    tmp.unlink(missing_ok=True)
    try:
        con.execute(f"""
            COPY (
                SELECT *
                FROM sqlite.raw_measurements
                WHERE timestamp >= {start_ms} AND timestamp < {end_ms}
                ORDER BY timestamp
            ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)
        """)
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return count


def run(db_path: str, out_dir: Path, days_back: int = 1) -> None:
    """Export the last `days_back` complete days that don't have a Parquet yet."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path}' AS sqlite (TYPE sqlite, READ_ONLY)")

    today = date.today()
    yesterday = today - timedelta(days=1)
    exported = 0
    for i in range(days_back, 0, -1):
        day = today - timedelta(days=i)
        dest = out_dir / f"{day}.parquet"
        if dest.exists() and day < yesterday:
            log.debug("skip %s — already exported", day)
            continue
        count = export_day(con, day, out_dir)
        if count:
            log.info("exported %s → %s (%d rows)", day, dest, count)
            exported += 1
        else:
            log.debug("skip %s — no data", day)

    con.close()
    log.info("done — %d day(s) exported", exported)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=_DB_DEFAULT)
    parser.add_argument("--archive", default=str(_ARCHIVE_DIR))
    parser.add_argument("--days-back", type=int, default=30,
                        help="How many past days to check (skips existing files)")
    args = parser.parse_args()
    run(args.db, Path(args.archive), args.days_back)
