# petascale — Claude Code instructions

## Project overview

petascale is a local-first pet observability platform. It captures sensor data
from Home Assistant, detects pet events in real-time, and produces analytics for
dashboards and future ML models. Primary users: homelab operators with multiple
pets.

Hardware context: ESP32 microcontrollers with HX711 load cells (50kg for litter
box, smaller for water/food bowls). Future: ESP32-S3 with cameras running YOLO
for per-cat recognition.

See `docs/requirements.md`, `docs/architecture.md`, `docs/resilience.md`, and
`docs/stack-decisions.md` for full design context. Read these at the start of
every session.

## Key design principles

- **Preservation first.** Raw sensor data is sacred; never mutate it. All derived
  data is recomputable from raw.
- **Two-speed architecture.** Hot path (seconds) for live events, warm path
  (minutes) for refined detection, cold path (daily) for aggregates/features.
- **Idempotent replay.** Every job keyed by time window, safe to re-run.
- **Lean over enterprise.** Prefer SQLite over Postgres, Python daemon over
  Kafka, static sites over SPA frameworks.
- **HA is the capture buffer.** Treat HA's recorder as our ~7-day upstream
  queue. Checkpoint + backfill on reconnect; never lose data because we were
  offline.

## Stack decisions (locked)

- **Language:** Python 3.12
- **Package manager:** uv (not pip/poetry)
- **Linter/formatter:** ruff (configured in pyproject.toml)
- **Tests:** pytest
- **Primary store:** SQLite in WAL mode
- **Bus for events:** MQTT (Mosquitto)
- **Archive format:** Parquet, partitioned by date
- **Analytics engine:** DuckDB (reads SQLite directly + Parquet)
- **Off-site backup:** Litestream → Backblaze B2
- **Deployment target:** Docker container on Proxmox LXC
- **Dashboard:** Evidence.dev (static site with DuckDB-WASM)
- **HA integration:** subscribe via websocket or MQTT; publish detected events
  back via MQTT

## Hard rules

- **Never put SQLite on NFS.** Use local SSD or iSCSI block storage.
- **Never delete raw data.** Archive or retire, never delete.
- **Never write a single file over ~500 lines** without splitting into modules.
  If a file is growing past that, refactor first.
- **Always include tests** for any new module or non-trivial function.
- **Never commit secrets** — use environment variables and `.env.example`.
- **Never mutate Parquet files** in the archive once written. Regenerate and
  replace if needed.

## Coding conventions

- Use `pydantic` models for data (sensor readings, events, features)
- Use `structlog` for logging, JSON output
- Async I/O via `asyncio` and `aiomqtt` where natural; sync SQLite is fine
- Type hints required on public functions
- Docstrings in Google style
- Prefer composition over inheritance; keep modules small and testable

## Project structure

src/petascale/
ingest/    # HA subscriber, hot-path event detection
warm/      # 5-minute reprocessor
cold/      # Nightly aggregator, Parquet export
store/     # SQLite access layer, schema, migrations
events/    # Event models, state machines
health/    # Watchdog and health checks
tests/
scripts/     # One-off utilities (backfill imports, etc.)
docker/
dashboard/   # Evidence.dev site (added later)
docs/        # Design documents, architecture decisions

## Workflow expectations

- Propose a plan before making non-trivial changes (use plan mode for anything
  touching multiple files)
- Commit in small, coherent units with conventional-commit style messages
- Don't run destructive commands (rm, migrations, pushes) without asking
- When a decision is ambiguous, ask rather than guess
- Update the relevant doc in `docs/` when a design decision changes
