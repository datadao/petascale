# Runbook — petascale maintenance

## Data flow

```
ESP32 sensors
    │ MQTT publish (live, ~1 Hz)
    ▼
petascale-daemon
    ├─ writes raw_measurements → SQLite (petascale.db)
    └─ on startup: HA REST backfill → fills gap since last checkpoint

petascale-warm (every 60 s)
    └─ reads last 15 min of raw → polars detection pipeline → upserts events

petascale-analytics (every 5 min loop)
    ├─ aggregator  → exports complete days to /data/archive/YYYY-MM-DD.parquet
    └─ dashboard   → renders dashboard.html from SQLite (today) + Parquet (older days)

nginx
    └─ serves dashboard.html at http://petascale-ingest.local:8080
```

## What feeds the dashboard

| Chart | Source | Window |
|---|---|---|
| Data density heatmap | raw_measurements (all time) | All available data |
| Raw weight traces | raw_measurements (all time) | Last 24 hours |
| Per-cat potty weights | events (potty, cat IS NOT NULL) | Last 30 days |
| Daily event counts | events (potty by cat + cleaning) | Last 30 days |

SQLite holds live data (today + HA backfill up to ~10 days).
Parquet holds complete closed days — yesterday is always re-exported in case it was partial.
The dashboard view stitches them without double-counting.

## Schedules (homelab)

| Job | When | What it does |
|---|---|---|
| HA backfill | Daemon startup only | Fills gap from last checkpoint to now via HA REST API |
| Warm-path detection | Every 60 s (warm container) | Re-runs detection over the last 15 min, upserts events |
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

# Force events backfill over a historical window (e.g. after consolidated.csv import)
docker exec petascale-warm python scripts/backfill_events.py \
    --db /data/petascale.db \
    --sensor sensor14/cat_weight_sensor \
    --since 2025-08-13 --until 2026-05-03

# Wipe + recompute all events (idempotent re-run is safer than DELETE; only wipe
# if you changed the algorithm or thresholds)
sqlite3 /data/petascale/petascale.db "DELETE FROM events;"
docker compose -f docker/docker-compose.yml restart warm
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

# Events detected (last 30 days, by type and cat)
sqlite3 /data/petascale/petascale.db \
  "SELECT date(timestamp/1000,'unixepoch','localtime') AS day,
          type, COALESCE(cat,'-') AS cat, count(*) AS n
   FROM events
   WHERE timestamp >= (strftime('%s','now') - 30*86400) * 1000
   GROUP BY day, type, cat ORDER BY day, type;"

# Recent potty events with weights
sqlite3 /data/petascale/petascale.db \
  "SELECT datetime(timestamp/1000,'unixepoch','localtime') AS ts,
          cat, weight_g, cat_distance_g
   FROM events WHERE type='potty' ORDER BY timestamp DESC LIMIT 20;"

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

# Warm-path detection (60s loop)
docker compose -f docker/docker-compose.yml logs warm -f

# Analytics (Parquet export + dashboard render)
docker compose -f docker/docker-compose.yml logs analytics -f

# TrueNAS backup
journalctl -u petascale-backup -n 50
```

---

## Cat profile calibration (homelab)

`identify_cat` accepts a reading only if it lands in exactly one profile's
`[weight_g - slop_g, weight_g + slop_g]` window. Outside every window, or
inside two, the visit is stored with `cat = NULL` — and since every dashboard
query filters `cat IS NOT NULL`, it disappears from the UI rather than raising
anything. **A cat that quietly stops appearing is a profile problem until
proven otherwise.**

Sizing `slop_g`: the plateau estimator is tight — across 75 clean visits the
spread around the same-day mean was 42 g SD, ±123 g worst case. Everything
above ~150 g of slop is therefore budget for *drift between calibrations*, not
for noise. A growing cat needs `weight_g` re-centred every few months; a 700 g
slop on a cat gaining ~8 g/day buys roughly two months before visits start
dropping out.

```bash
# What the unattributed visits weigh — the cluster is the cat that fell out
sqlite3 /data/petascale/petascale.db "
SELECT date(timestamp/1000,'unixepoch') AS day, count(*) AS n,
       min(weight_g), round(avg(weight_g)) AS avg_g, max(weight_g)
FROM events WHERE type='potty' AND cat IS NULL AND algo='v1'
GROUP BY 1 ORDER BY 1;"

# Per-cat weight trend — watch for a profile the cat is drifting out of
sqlite3 /data/petascale/petascale.db "
SELECT cat, date(timestamp/1000,'unixepoch') AS day, count(*) AS n,
       round(avg(weight_g)) AS avg_g
FROM events WHERE type='potty' AND cat IS NOT NULL AND algo='v1'
GROUP BY 1,2 ORDER BY 2;"

# Visits misfiled as cleanings: a failed identify also breaks the
# baseline-adapted-exit rescue in pipeline.py, so the visit lands as a cleaning
sqlite3 /data/petascale/petascale.db "
SELECT datetime(timestamp/1000,'unixepoch') AS ts, weight_g
FROM events WHERE type='cleaning' AND algo='v1' AND weight_g < -3000
ORDER BY timestamp;"
```

After editing `config/cats.local.toml` (gitignored — it must be edited on the
LXC directly, it does not travel via git):

```bash
docker restart petascale-warm          # config is read once at startup
journalctl -t petascale-warm -n 20     # confirm the windows it logged
```

Startup logs one `Cat profile <name>: accepts <lo>-<hi> g` line per cat and an
ERROR per overlapping pair. Overlap is deliberately not fatal — the warm
container restarts `unless-stopped`, so raising would crash-loop and stop
detection altogether, which is worse than attributing nothing.

**Bringing a cat back onto the system** (new box, or one that moved rooms):
their old `weight_g` is stale the moment they stop being measured. Widen
`slop_g` enough to catch them at all, watch the unattributed-weights query for
their cluster, then re-centre `weight_g` on it and tighten `slop_g` back down
until the windows are disjoint. `pytest tests/test_config.py` asserts the
deployed profiles do not overlap.

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard frozen | Browser cache | Hard refresh: Cmd+Shift+R |
| Dashboard shows old data | Analytics container down | `docker compose up -d analytics` |
| Gap in heatmap | MQTT was down, backfill missed it | Delete stale Parquet for that day, clear checkpoint, restart daemon |
| Daemon can't reach MQTT | `.local` DNS doesn't resolve in container | Use IP in `MQTT_HOST` in `.env` on LXC |
| sqlite3 not found | Not installed | `apt-get install -y sqlite3` |
| No new events appearing | Warm container down or DB lock | `docker compose ps`; check warm logs; verify `raw_measurements` is current |
| Cat shows up as null | Weight outside every profile's `slop_g` — most often a growing cat that has drifted past his window | Check the unattributed weights (query below), re-centre `weight_g` in `config/cats.local.toml`, restart warm |
| Cat shows up as null, logs say "Ambiguous weight" | Two profiles' windows overlap; ambiguous readings are deliberately left unattributed rather than misattributed | Shrink `slop_g` until the windows are disjoint, restart warm |
| A cat silently stops appearing | Every dashboard query filters `cat IS NOT NULL`, so unattributed visits vanish from all charts instead of showing up as errors | Query unattributed events directly (below); consider it first whenever a cat "disappears" |
| Acceptance test fails after change | Algorithm regression | Run `pytest tests/test_pipeline_acceptance.py`; compare to `tests/fixtures/expected_events.json` |

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
