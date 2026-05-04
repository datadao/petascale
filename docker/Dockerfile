# syntax=docker/dockerfile:1
# ── build stage ───────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install deps first (cached layer unless pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Install project
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/            src/
COPY scripts/        scripts/
COPY config/         config/
COPY run_daemon.py   .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

# /data is mounted from the host (local SSD) — never put SQLite on NFS
VOLUME ["/data"]

CMD ["python", "run_daemon.py"]
