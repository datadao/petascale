# Runbook — petascale maintenance

## Data flow

```
ESP32 sensors
    │ MQTT publish (live, ~1 Hz)
    ▼
petascale-daemon
    ├─ writes raw_measurements → SQLite (petascale.db)
    └─ on startup: HA REST backfill → fills gap since last checkpoint

petascale-analytics (every 5 min loop)
    ├─ aggregator  → exports complete days to /data/archive/YYYY-MM-DD.parquet
    └─ dashboard   → renders dashboard.html from SQLite (today) + Parquet (older days)

nginx
    └─ serves dashboard.html at http://petascale-ingest.local:8080
```

## What feeds the dashboard

| Chart | Source | Window |
|---|---|---|
| Data density heatmap | SQLite + Parquet (all time) | All available data |
| Raw weight traces | SQLite + Parquet (all time) | Last 24 hours |

SQLite holds live data (today + HA backfill up to ~10 days).
Parquet holds complete closed days — yesterday is always re-exported in case it was partial.
The dashboard view stitches them without double-counting.

## Schedules (homelab)

| Job | When | What it does |
|---|---|---|
| HA backfill | Daemon startup only | Fills gap from last checkpoint to now via HA REST API |
| Parquet aggregator | Every 5 min (analytics container) | Exports missing days; always re-exports yesterday |
| Dashboard render | Every 5 min (analytics container) | Regenerates dashboard.html from SQLite + Parquet |
| TrueNAS backup | 03:00 nightly (systemd timer on LXC) | rsync SQLite + Parquet to SMB share |

---

## Force commands (homelab)

```bash
# Force dashboard regeneration now
docker exec petascale-analytics python scripts/dashboard.py

# Force Parquet export for all missing days (last 30 days)
docker exec petascale-analytics python -m petascale.cold.aggregator

# Force re-export a specific day (delete file first so it's not skipped)
rm /data/petascale/archive/YYYY-MM-DD.parquet
docker exec petascale-analytics python -m petascale.cold.aggregator

# Force full HA re-backfill (clears checkpoint — re-fetches all 10 days from HA)
sqlite3 /data/petascale/petascale.db "DELETE FROM ingestion_checkpoints;"
docker compose -f docker/docker-compose.yml restart daemon
```

---

## Check data health (homelab)

```bash
# Row count by day
sqlite3 /data/petascale/petascale.db \
  "SELECT date(timestamp/1000,'unixepoch','localtime') as day, count(*)
   FROM raw_measurements GROUP BY day ORDER BY day;"

# Latest reading and total
sqlite3 /data/petascale/petascale.db \
  "SELECT count(*) AS total,
          datetime(max(timestamp)/1000,'unixepoch','localtime') AS newest
   FROM raw_measurements;"

# Events detected
sqlite3 /data/petascale/petascale.db \
  "SELECT event_type, count(*) FROM hot_events GROUP BY event_type;"

# Backfill checkpoint per sensor
sqlite3 /data/petascale/petascale.db \
  "SELECT sensor_id, datetime(last_timestamp/1000,'unixepoch','localtime')
   FROM ingestion_checkpoints;"

# Parquet archive files
ls -lh /data/petascale/archive/
```

---

## Logs (homelab)

```bash
# Daemon (MQTT ingest + backfill)
docker compose -f docker/docker-compose.yml logs daemon -f

# Analytics (Parquet export + dashboard render)
docker compose -f docker/docker-compose.yml logs analytics -f

# TrueNAS backup
journalctl -u petascale-backup -n 50
```

---

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard frozen | Browser cache | Hard refresh: Cmd+Shift+R |
| Dashboard shows old data | Analytics container down | `docker compose up -d analytics` |
| Gap in heatmap | MQTT was down, backfill missed it | Delete stale Parquet for that day, clear checkpoint, restart daemon |
| Daemon can't reach MQTT | `.local` DNS doesn't resolve in container | Use IP in `MQTT_HOST` in `.env` on LXC |
| sqlite3 not found | Not installed | `apt-get install -y sqlite3` |

---

## Dev machine commands

```bash
# Export raw readings to CSV (goes to .private/, gitignored)
sqlite3 -header -csv petascale.db "SELECT * FROM raw_measurements ORDER BY timestamp;" \
  > .private/raw_measurements_backup_$(date +%Y%m%d).csv

# Force full HA re-backfill locally
sqlite3 petascale.db "DELETE FROM ingestion_checkpoints;"
uv run python run_daemon.py

# Regenerate dashboard locally
uv run --extra analytics python scripts/dashboard.py \
  --db petascale.db --archive /tmp/archive --out dashboard.html
open dashboard.html

# Check DB health
sqlite3 petascale.db "
SELECT date(timestamp/1000, 'unixepoch', 'localtime') AS day, count(*) AS readings
FROM raw_measurements GROUP BY 1 ORDER BY 1;"

# Run tests
uv run pytest
```
