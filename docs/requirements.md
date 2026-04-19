# Requirements

## Preservation (primary goal)

- Capture real-time weight sensor data published by ESP32 devices via MQTT
- HA recorder (~10-day retention) acts as upstream buffer for gap recovery — not
  the primary source
- Backfill from HA on daemon startup to close gaps caused by downtime or restarts
- Durable, backed up, survives hardware failure and bad experiments
- Run on existing homelab: Proxmox + TrueNAS + Docker

## Historical data migration (one-time)

- Import ~1 year of sensor data from the previous system into `raw_measurements`
- Source formats: CSV files, DuckDB databases — exact schema TBD per file
- Data must be normalized to the petascale schema (sensor_id, sensor_type, value,
  timestamp ms) before insertion
- Idempotent: safe to re-run; UPSERT on `(sensor_id, timestamp)` prevents duplicates
- Migration script lives in `scripts/` — not part of the daemon
- Raw source files preserved in `.private/` and never deleted

## Data sources

- **Primary (live):** MQTT broker — ESP32 devices publish calibrated readings
  (grams, not raw ADC) to `sensors/<device>/sensor/<name>/state`
- **Secondary (backfill):** HA REST history API — used on startup to fill gaps
  since last checkpoint; limited to HA recorder retention (~10 days)
- **Future (raw ADC):** ESP32 to publish raw HX711 values on a separate topic
  to enable app-side calibration and re-calibration without reflashing firmware

## Sensor configuration

- Sensors defined in `config/sensors.toml` — id, HA entity, name, role
- Global detection thresholds in the same config (enter/exit grams, stability window)
- Sensor metadata mirrored into SQLite `sensors` table for queryability
- Adding a sensor requires only a config entry — no code change

## Processing

- Real-time event detection on the hot path: presence (cat/dog on sensor),
  left (session end with duration + median weight)
- Events fire via state machine: IDLE → CAT_ENTERING → CAT_PRESENT → CAT_LEAVING → IDLE
- Later batch/ML processing: per-animal weight trending, behavioral baselines,
  anomaly detection
- Support iterative algorithm improvements by re-running jobs without touching raw data

## Storage

- **Primary store:** SQLite in WAL mode (`petascale.db`) — single writer (daemon),
  many readers
- **Raw data is immutable:** never update or delete `raw_measurements` rows
- **WAL checkpoint** must run periodically to prevent unbounded WAL file growth
- **Long-term archive:** nightly Parquet export partitioned by date (cold path, not yet built)
- **Off-site backup:** Litestream → Backblaze B2 for continuous WAL replication (not yet built)
- SQLite must live on local SSD or iSCSI block storage — never NFS

## Ingest robustness

- Daemon reconnects automatically on MQTT broker disconnect
- Per-message exception handling — one bad message cannot kill the daemon
- Graceful shutdown on SIGINT/SIGTERM
- Health check endpoint needed before production deploy
- Backfill retries individual day windows on failure (not yet implemented)
- Periodic gap-filling (re-backfill since last checkpoint) to catch MQTT drops
  during runtime (not yet implemented)

## Consumption

- Home Assistant dashboard widget with live pet status via MQTT events
- Analytics site (Evidence.dev) reading Parquet + SQLite via DuckDB
- Ad-hoc analysis and ML feature engineering via DuckDB
- Quick local dashboard: `scripts/dashboard.py` generates `dashboard.html`

## Constraints

- Lean — minimum viable infrastructure, no enterprise complexity
- Fully open source preferred, no licensing surprises
- Semi-free cloud backup acceptable (e.g., Backblaze B2)

## Context

- Cats and dogs are weight-monitored for health tracking (weight gain/loss/maintenance).
  Multi-species, multi-individual tracking needed.
- Existing hardware: ESP32 with HX711 load cells (50kg for litter box, smaller for
  food/water bowls). Calibration currently hardcoded in ESPHome firmware.
- Future scope: ESP32-S3 cameras with YOLO for per-animal recognition by color
  (orange vs. white cat), sleep location detection in cat trees or dog beds,
  activity classification.
