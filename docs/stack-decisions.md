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

## Detection engine: Polars

Chosen for: tiny dependency footprint (~15 MB) compared to pandas (~50 MB)
relevant for the warm-path container; first-class time-based rolling
windows (`rolling_mean_by`); native `forward_fill(limit=...)` matching the
spec's ffill horizon cap; clean expressions for IQR + mode.

**Rejected:**
- **Pandas** — heaviest of the candidates; only edge it had over polars was
  literal pseudocode parity with the algorithm spec, which is not worth a
  35 MB image cost on a 512 MB LXC.
- **DuckDB-only** — already in the analytics extra and stack-aligned, but
  recursive CTEs for segment merging and IQR/mode in SQL hurt readability of
  `detect/segments.py` and `detect/plateau.py`.
- **Pure NumPy** — feasible but ~2× the LOC for time-rolling and ffill,
  with no maintenance benefit.

The `detect/*` modules are pure functions, so swapping engines later is
contained. The acceptance fixture in `tests/fixtures/expected_events.json`
is the engine-agnostic contract.

## Events store: SQLite (TODO: revisit)

Events live in the same SQLite DB as raw measurements, primary key
`(sensor_id, timestamp, type)` with `ON CONFLICT DO NOTHING` for
idempotent re-runs. The spec in `.private/algo/algo.md` D18 proposed
DuckDB; we kept SQLite for stack consistency.

**Revisit when** any of:
- events table grows beyond ~1M rows (queries get slow);
- analytical queries want to live in the same engine that holds the truth;
- we move analytics to a unified DuckDB-on-SQLite-attach pattern and the
  attach overhead becomes the bottleneck.

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
