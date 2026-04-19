# Resilience & Fault Tolerance

## Core principle

Capture-at-source is sacred. Everything downstream is replayable.

Home Assistant's recorder itself buffers ~7 days of state history, so if our
ingestion daemon is offline for hours we can fetch the missed range from HA's
REST API on reconnect. This makes most transient downstream failures a
non-event.

## Failure modes and mitigations

### Power loss to the house
- Small UPS on the Proxmox host ($60-150) covers most blips
- On extended outage, everything shuts down cleanly
- On restore: HA comes up, daemon comes up, daemon backfills from HA's
  recorder, pipeline catches up automatically

### Proxmox host reboot
- Docker/LXC auto-start
- Daemon sees checkpoint gap, backfills

### TrueNAS reboot or NFS hiccup
- SQLite is NOT on NFS (hard rule) — this failure mode is sidestepped
- Parquet archive on NFS can tolerate brief unavailability; cold path retries

### Ingestion daemon crash
- systemd / Docker `restart: unless-stopped` brings it back
- Backfill from HA recorder covers the gap
- In-memory bounded queue buffers writes during SQLite unreachability

### Warm/cold path job failure
- Jobs are idempotent and window-keyed
- `job_runs` table tracks which windows completed
- On schedule, any missing window triggers automatic re-run

### Sensor offline (battery dead, network glitch)
- Monitoring: alert if no reading from a specific sensor in N hours
- Data genuinely lost for duration, not recoverable — design accepts this

### Network partition
- Same as daemon crash — backfill on reconnect

### Storage corruption
- `PRAGMA integrity_check` runs weekly
- Litestream → B2 provides point-in-time recovery
- ZFS snapshots on the containing dataset give another recovery axis

### Operator error (wrong rm, bad DELETE)
- Litestream generations allow rolling back to a point before the mistake
- ZFS snapshots similarly
- Parquet archive is immutable — even if SQLite is wiped, historical data
  survives

## Design patterns

### Checkpoint + backfill

The ingestion daemon maintains an `ingestion_checkpoint` table:
sensor_id TEXT PRIMARY KEY
last_captured_ts INTEGER
last_updated_at INTEGER
On startup, for each sensor, compare checkpoint to now. If gap > threshold,
call HA's REST history API for the missed range, UPSERT into raw table, then
enter live subscription mode.

### Idempotent writes

All raw readings use a composite natural key (sensor_id, ts) with UPSERT
semantics. Re-ingesting the same reading is a no-op. Enables safe replay.

### Window-keyed derived outputs

All warm/cold outputs keyed by `(window_start, window_end)` or `date`. Writing
uses `INSERT OR REPLACE`. A partial or corrupted run can be cleanly redone.

### Job run ledger

A `job_runs` table: `(job_name, window_start, window_end, status, started_at,
finished_at, rows_written, error_msg, code_version)`. Scheduler checks ledger
before skipping a window; automatically re-runs anything `FAILED` or missing.

### Versioned derived data

Every derived row carries `code_version` (git commit of the script that
produced it). Improving an algorithm means bumping the version and letting the
scheduler re-process. Can A/B compare old vs. new output before committing.

## Monitoring

Hourly health check writes to `health_check` table:
- Fresh reading from each expected sensor within N minutes?
- Did today's cold path job succeed?
- Is yesterday's Parquet file present and plausible size?
- Does `integrity_check` pass?
- Is Litestream replicating (B2 bucket last-modified within threshold)?

Failures surface via HA sensor entities so they're visible in the dashboard
and trigger notifications.

## Optional upgrade: MQTT-first topology

If we want survival of HA outages themselves (not just daemon outages), point
sensors at an MQTT broker as primary bus:

Sensor → Mosquitto (persistent) → both HA and petascale daemon subscribe

Now HA and our daemon are peers. Either can be down without losing data.
Mosquitto with persistence enabled holds recent messages on disk across broker
restarts.
