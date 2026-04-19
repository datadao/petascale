# petascale — Tasks

## Legend
- [x] Done and verified  [ ] Not started  [~] In progress / partial

---

## 0 · Immediate: local dev baseline

Goal: daemon runs on Mac, connects to real HA, stores readings, prints events.
No homelab required.

- [x] Fix pytest: add `pythonpath = ["src"]` to pyproject.toml, set asyncio_mode = "auto"
- [x] Verify tests pass: `uv run pytest`
- [x] Make MQTT optional at startup (skip publish if not configured, log warning)
- [x] Fix state machine: CAT_ENTERING → CAT_PRESENT transition fires event too early
- [ ] Smoke test: connect to real HA, ingest 5 minutes of weight readings, confirm SQLite rows
- [ ] Local replay script: feed recorded HA history JSON through daemon offline (no live HA needed)

---

## 1 · Foundation (scaffold exists, needs verification)

- [~] pyproject.toml + uv setup
- [~] Pydantic models: SensorReading, DetectedEvent, SensorType, EventType
- [~] PresenceStateMachine (exists, has bugs — fix in phase 0)
- [~] SQLite store: WAL mode, raw_measurements, hot_events, ingestion_checkpoints
- [~] HA websocket subscriber + backfill on startup
- [~] MQTT publish of detected events
- [x] tests/test_events.py — state machine unit tests (missing)
- [ ] tests/test_daemon.py — integration test with mock HA websocket (missing)

---

## 2 · Warm path

- [ ] `src/petascale/warm/reprocessor.py` — 5-min cron job
- [ ] Re-run presence detection with longer context window
- [ ] Write to `warm_events` with `supersedes_hot_event_id`
- [ ] Test: warm events don't overwrite raw or hot data

---

## 3 · Cold path + Parquet

- [ ] `src/petascale/cold/aggregator.py` — nightly job
- [ ] Daily aggregates: per-sensor stats, event counts, weight trends
- [ ] Export immutable Parquet files partitioned by date
- [ ] DuckDB query examples for ad-hoc analysis

---

## 4 · Dashboard

- [ ] Evidence.dev site scaffold in `dashboard/`
- [ ] Connect to DuckDB reading Parquet + SQLite
- [ ] Charts: weight trend per sensor, event timeline, daily summaries
- [ ] HA Lovelace card integration via MQTT entities

---

## 5 · Homelab deployment (IaC)

Goal: `make deploy` from Mac → full stack running in Proxmox LXC.

- [x] `docker/Dockerfile` — multi-stage, uv-based
- [x] `docker/docker-compose.yml` — daemon with journald logging, /data volume
- [x] `docs/deployment.md` — Proxmox LXC setup instructions
- [ ] Move `duckdb` + `plotly` to optional deps so production image is leaner
- [ ] `infra/ansible/` — Ansible playbook: provision LXC, install Docker, clone repo
- [ ] Secrets: env vars injected via Ansible vault or LXC config
- [ ] Litestream config → Backblaze B2
- [ ] Health check endpoint (simple HTTP ping)
- [ ] CI: GitHub Actions — lint + test on push

---

## 6 · Off-site backup + resilience

- [ ] Litestream WAL replication running and verified
- [ ] Backfill logic tested: kill daemon, restart, confirm no gap
- [ ] Alerting: sensor silence > N minutes → MQTT alert to HA

---

## 7 · Future / ML

- [ ] ESP32-S3 + YOLO for per-animal recognition
- [ ] Per-animal weight attribution (currently per-sensor)
- [ ] Behavioral baselines + anomaly detection
- [ ] FastAPI + native iOS app
