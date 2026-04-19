# Runbook — petascale maintenance

## Export raw readings to CSV

Run from the repo root. Exports the full `raw_measurements` table, preserving all raw sensor data.

```bash
sqlite3 -header -csv petascale.db "SELECT * FROM raw_measurements ORDER BY timestamp;" \
  > .private/raw_measurements_backup_$(date +%Y%m%d).csv
```

Output goes to `.private/` (gitignored). Filename includes today's date, e.g. `raw_measurements_backup_20260418.csv`.

---

## Force a full backfill from HA

Use when the DB is empty or checkpoints are stale. Fetches up to 10 days of history (HA recorder retention) day by day.

```bash
# 1. Clear checkpoints so backfill starts from scratch
sqlite3 petascale.db "DELETE FROM ingestion_checkpoints;"

# 2. Run backfill only (no MQTT)
PYTHONPATH=src uv run python -c "
import asyncio, os, logging
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO)
from petascale.store import Store
from petascale.ingest.ha_backfill import run_backfill, parse_sensor_map

async def main():
    store = Store(os.getenv('DB_PATH', 'petascale.db'))
    await store.initialize()
    await run_backfill(store, os.environ['HA_URL'], os.environ['HA_TOKEN'],
                       parse_sensor_map(os.environ['HA_BACKFILL_SENSORS']))
    await store.close()

asyncio.run(main())
"
```

Requires `HA_URL`, `HA_TOKEN`, and `HA_BACKFILL_SENSORS` in `.env`.

---

## Regenerate dashboard

```bash
uv run python scripts/dashboard.py && open dashboard.html
```

---

## Check DB health

```bash
# Row counts per day
sqlite3 petascale.db "
SELECT date(timestamp/1000, 'unixepoch', 'localtime') AS day, count(*) AS readings
FROM raw_measurements GROUP BY 1 ORDER BY 1;"

# Date range and total
sqlite3 petascale.db "
SELECT count(*) AS total,
       datetime(min(timestamp)/1000, 'unixepoch', 'localtime') AS oldest,
       datetime(max(timestamp)/1000, 'unixepoch', 'localtime') AS newest
FROM raw_measurements;"

# Checkpoints
sqlite3 petascale.db "SELECT sensor_id, datetime(last_captured_ts/1000,'unixepoch','localtime') FROM ingestion_checkpoints;"
```

---

## Run the daemon

```bash
uv run python run_daemon.py
```

Starts MQTT ingestion (live) and runs HA backfill on startup to close any gaps since last checkpoint.
