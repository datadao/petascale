# Architecture

## Layers

### Layer 0 — Source
Home Assistant sensors (weight, motion, litterbox, feeder, water, future
cameras).

### Layer 1 — Ingestion (raw capture only)
The `petascale-ingest` daemon:
- Subscribes to HA events via MQTT (and runs an HA REST backfill on boot
  to close gaps caused by daemon downtime)
- Writes raw readings to the primary store
- Does not detect events itself — detection is intrinsically batch
  (segment-based plateau extraction needs the full segment in hand)

### Layer 2 — Primary Store
SQLite file in WAL mode. The ingestion daemon writes `raw_measurements`;
the warm daemon writes `events`. Both processes share the DB safely
through WAL (concurrent reads + serialized writes).

### Layer 3 — Warm Path (`petascale-warm`, ~60 s cadence)
Separate process running the polars-based detection pipeline
(`petascale.detect.*`) over a sliding 15-minute window of raw readings.
Each tick: resample → anomaly mask → segment merge → classify
(potty / cleaning) → plateau-mode for cat weight → match against
configured cat profiles. Idempotent: events keyed
`(sensor_id, timestamp, type)` with `ON CONFLICT DO NOTHING`.

Run as a separate container so a polars/pipeline crash does not affect raw
capture. Algorithm spec lives in `.private/algo/algo.md`; the acceptance
fixture in `tests/fixtures/expected_events.json` is the regression gate.

### Layer 4 — Cold Path (nightly)
Batch job that computes daily aggregates, features, trends, anomaly scores.
Writes derived tables and exports immutable daily Parquet snapshots for the
dashboard and future ML.

### Layer 5 — Archive
Immutable Parquet files on TrueNAS, partitioned by date. Plus TrueNAS snapshots
on the whole dataset.

### Layer 6 — Off-site Backup
Litestream replicating SQLite WAL continuously to Backblaze B2. Restic for the
Parquet archive on a nightly schedule (optional).

### Layer 7 — Consumption
- HA dashboard via MQTT → HA entities → Lovelace cards
- Analytics site (Evidence.dev / Observable) reading Parquet via DuckDB-WASM
- Optional FastAPI + native iOS app

## Flow

┌──────────────────────────────────────────────────────────┐
│  Home Assistant (sensors)                                 │
└──────────────────────────────────────────────────────────┘
│ websocket events / MQTT
▼
┌──────────────────────────────────────────────────────────┐
│  petascale-ingest (Python daemon)                         │
│  • subscribes to HA via MQTT                              │
│  • HA REST backfill on boot                               │
│  • writes raw readings → SQLite raw_measurements          │
└──────────────────────────────────────────────────────────┘
│
▼
┌──────────────────────────────────────────────────────────┐
│  petascale.db (SQLite, WAL mode)                          │
│   ├── raw_measurements                                    │
│   ├── events                                              │
│   ├── sensors                                             │
│   └── ingestion_checkpoints                               │
└──────────────────────────────────────────────────────────┘
│           ▲
│ reads     │ writes
│           │
▼           │
┌──────────────────────────────────────────────────────────┐
│  petascale-warm (60 s loop, polars)                       │
│  • detect.{resample,anomaly,segments,plateau,classify}    │
│  • upserts (sensor_id, timestamp, type) → events          │
└──────────────────────────────────────────────────────────┘
│
┌───────────┼──────────┐
▼           ▼          ▼
┌─────────┐ ┌────────┐ ┌──────────┐
│ cold    │ │ DuckDB │ │ HA       │
│ aggreg. │ │ readers│ │ Lovelace │
│ (nightly│ │ (dash) │ │ (links)  │
└─────────┘ └────────┘ └──────────┘

## Warm-path detection pipeline (weight sensor)

Pure-function pipeline composed in `petascale.detect.pipeline.run`:

```
raw irregular samples (last 15 min)
   → resample to 1 Hz grid, ffill capped at 300 s    (resample.py)
   → flag points diverging from 5 s rolling mean     (anomaly.py)
   → merge ±60 s buffers around abnormal points      (segments.py)
   → for each segment, classify by start→end delta   (classify.py)
        end > start + 10 g    → potty (if peak−start ≥ 500 g)
        end < start − 10 g    → cleaning (if drop ≥ 100 g)
        otherwise             → drop (paranormal)
   → for potty: plateau-mode of high-pass IQR-filtered values  (plateau.py)
   → match plateau − start_value to cat profile (slop_g)        (identify.py)
```

All thresholds live in `[detection.litterbox]` in `config/sensors.toml`;
cat profiles in `[[cats]]`. The full algorithm spec and design decisions
D1–D20 are in `.private/algo/algo.md`. Acceptance gate:
`tests/test_pipeline_acceptance.py` (3 events ±2 s ±20 g, 6 filtered
segments must not appear).

## Total footprint

One SQLite file, four containers on a single LXC (`daemon`, `warm`,
`analytics`, `dashboard`/nginx), one MQTT broker (separate LXC), one
Litestream process. Estimated RAM under 250 MB. Polars in the warm
container is the single largest dep at ~15 MB.
