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
- [x] Smoke test: connect to real HA, ingest 5 minutes of weight readings, confirm SQLite rows
- [ ] Local replay script: feed recorded HA history JSON through daemon offline (no live HA needed)

---

## 1 · Foundation (scaffold exists, needs verification)

- [x] pyproject.toml + uv setup
- [x] Pydantic models: SensorReading, DetectedEvent, SensorType, EventType
- [x] PresenceStateMachine (bugs fixed in phase 0)
- [x] SQLite store: WAL mode, raw_measurements, hot_events, ingestion_checkpoints
- [x] HA REST backfill on startup (10-day window, checkpoint-based)
- [~] MQTT publish of detected events (optional, skipped if not configured)
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

- [x] `src/petascale/cold/aggregator.py` — exports daily Parquet, skips existing files
- [ ] Parquet files backed up to TrueNAS SMB share nightly
- [ ] DuckDB query examples for ad-hoc analysis
- [ ] Parquet consistency check: verify each file covers a full day (expected row count vs actual, first/last timestamp, no gaps > N minutes)

---

## 4 · Dashboard

- [x] Plotly dashboard served via nginx on port 8080 (regenerates every 5 min)
- [x] Dashboard reads SQLite directly + Parquet archive (unified DuckDB view)
- [x] Per-sensor weight traces (multi-sensor aware)
- [x] nginx index fix — dashboard.html served as root
- [ ] Event timeline chart (cat_present / cat_left events)
- [ ] HA Lovelace card integration via MQTT entities
- [ ] Evidence.dev site (future, heavier but richer)

---

## 5 · Homelab deployment (IaC)

Goal: `make deploy` from Mac → full stack running in Proxmox LXC.

- [x] `docker/Dockerfile` — multi-stage, uv-based
- [x] `docker/docker-compose.yml` — daemon with journald logging, /data volume
- [x] `docs/deployment.md` — Proxmox LXC setup instructions
- [x] Move `duckdb` + `plotly` to optional `[analytics]` extra — production image is lean
- [x] `docker/Dockerfile.analytics` — separate image for dashboard + cold aggregation
- [x] `docker/docker-compose.yml` — analytics + nginx dashboard services added
- [~] TrueNAS SMB backup: script + systemd timer ready (`infra/systemd/`), needs TrueNAS share + credentials set up on LXC
- [ ] `infra/ansible/` — Ansible playbook: provision LXC, install Docker, clone repo
- [ ] Secrets: env vars injected via Ansible vault or LXC config
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
