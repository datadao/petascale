# Stack Decisions

Why each piece was chosen, and what was rejected. Future-us should not
relitigate these without a reason.

## Primary store: SQLite (in WAL mode)

Chosen for: leanest operational overhead, embedded, HA already uses it
natively, handles our throughput and volume easily, single-file backups
trivial, DuckDB reads it directly for analytics with no ETL.

**Rejected:**
- **InfluxDB 2.x** — maintenance mode; adopting tech on its way out.
- **InfluxDB 3 Core** — 72-hour query window makes long-term archives
  impossible.
- **InfluxDB 3 Enterprise** — licensing ambiguity; free-for-home but unstable
  commitment.
- **VictoriaMetrics** — no HA-native integration; optimized for metrics, not
  event-correlation queries.
- **Postgres (with or without Timescale)** — overkill footprint for our
  volume. Valid upgrade path later via `pgloader` if needed.
- **DuckDB as primary** — single-writer, single-process design conflicts with
  long-running HA daemon + separate reader processes.
- **DuckLake (DIY)** — interesting for analytics layer, but no HA-native
  ingestion; we'd build the bridge ourselves. Worth revisiting for archive
  layer.
- **BoilStream** — ~8GB RAM, enterprise feature set (Kafka, SAML, multi-
  tenant, full-text search). Wrong shape for one-person homelab cat tracking.

## Ingestion: single Python daemon

Chosen for: minimal moving parts, testable, direct HA websocket/MQTT
subscription, clear ownership of the write path.

**Rejected:**
- Kafka/Redpanda + stream processor — overkill for our data rate.
- HA automations as the pipeline — poor environment for stateful algorithms,
  rolling windows, ML inference.

## Event bus: MQTT (Mosquitto)

Chosen for: HA speaks it natively, persistence available, decouples
producers/consumers, established homelab tool.

## Archive: Parquet partitioned by date

Chosen for: columnar, compressed, readable by pandas/Polars/DuckDB, immutable
once written, plays well with ZFS snapshots and cloud object storage.

## Analytics engine: DuckDB

Chosen for: reads SQLite directly, reads Parquet directly, fast columnar
queries, no server to run, perfect fit for both cold-path aggregation and
future ML feature engineering.

## Off-site backup: Litestream → Backblaze B2

Chosen for: continuous WAL replication (near-zero RPO), ~5MB RAM overhead, B2
is $6/TB/month with generous free egress, restore is one command.

**Supplementary:** restic for nightly Parquet archive backup (dedup + encrypt).

## Dashboard: Evidence.dev

Chosen for: static site + DuckDB-WASM means no backend; Parquet file is the
data layer; deployable anywhere (TrueNAS, Cloudflare Pages, local).

**Rejected:**
- Grafana as primary — fine for ops dashboards, less nice for narrative
  per-cat pages.
- Superset/Metabase — heavier, needs a backend service.
- Custom React app — more work without corresponding benefit initially.

## HA integration path

- Hot events flow: daemon → MQTT → HA sensor entities → Lovelace.
- Deep analytics: iframe the Evidence site into HA, or link out.
- Possible future: publish a proper HA custom integration if the project
  grows up.
