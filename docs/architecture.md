# Architecture

## Layers

### Layer 0 — Source
Home Assistant sensors (weight, motion, litterbox, feeder, water, future
cameras).

### Layer 1 — Ingestion + Hot Path
A single Python daemon that:
- Subscribes to HA events via websocket or MQTT
- Writes raw readings to the primary store
- Runs a rolling-window state machine to detect events live
- Publishes detected events back to MQTT for HA to consume

### Layer 2 — Primary Store
SQLite file in WAL mode. Single writer (the ingestion daemon), many readers.
Receives raw readings and event records continuously.

### Layer 3 — Warm Path (optional, ~5 min cadence)
Cron/systemd job that re-runs event detection over recent raw data with better
context (motion correlation, longer baselines, per-cat attribution). Writes
corrected events to a separate table, never overwrites raw.

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
│  Ingestion daemon (Python, ~150 lines)                   │
│  • subscribes to HA events                                │
│  • writes raw readings → SQLite raw tables                │
│  • runs hot-path detector on rolling window (in-memory)   │
│  • publishes detected events → MQTT + SQLite events       │
└──────────────────────────────────────────────────────────┘
│                           │
│ MQTT events               │ SQLite writes
▼                           ▼
┌──────────────────┐       ┌────────────────────────────────┐
│  HA dashboard    │       │  petascale.db (SQLite, WAL mode)    │
│  • live status   │       │   ├── raw_measurements         │
│  • notifications │       │   ├── hot_events               │
└──────────────────┘       │   ├── warm_events (5min job)   │
│   ├── daily_features           │
│   └── cats (dim table)         │
└────────────────────────────────┘
│
┌───────────┼──────────┐
▼           ▼          ▼
┌─────────┐ ┌────────┐ ┌──────────┐
│ warm    │ │ cold   │ │ DuckDB   │
│ worker  │ │ worker │ │ readers  │
│ (5 min) │ │ (daily)│ │ (ad hoc) │
└─────────┘ └────────┘ └──────────┘
│           │          │
└───────────┼──────────┘
▼
┌──────────────────────┐
│  API / UI layer      │
│  • iPhone / web      │
│  • HA widget         │
│  • Evidence site     │
└──────────────────────┘

## Hot-path state machine (weight sensor)

IDLE ─── weight > 300g for 3 samples ──► CAT_ENTERING
│
│ weight stable (σ < 20g) for 5s
▼
CAT_PRESENT ─► publish MQTT event
│
│ weight < 100g for 2 samples
▼
CAT_LEAVING
│
▼
IDLE ─► publish session summary event
(duration, median weight)

When `CAT_PRESENT` fires: publish `cats/event/present` to MQTT.
When `IDLE` returns: publish `cats/event/left` with session duration + median
weight.

The warm-path job re-runs a better `detect_presence()` over the same raw window
5 minutes later, with extra context (motion in the room, baseline noise, etc.).
Writes to `warm_events` with a `supersedes_hot_event_id` column.

## Total footprint

One SQLite file, one MQTT broker (likely already running in HA), three Python
scripts on schedules, one static Evidence site, one Litestream process.
Estimated RAM across everything: under 200MB. Runs comfortably on a single
small LXC or Docker host.
