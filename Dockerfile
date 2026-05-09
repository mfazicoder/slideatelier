# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# slideAtelier production image — multi-stage.
#   Stage 1 (builder): installs deps with uv, compiles bytecode.
#   Stage 2 (runtime): copies only the .venv + source. No build tools.
# Drops privileges to a non-root `slideatelier` user, runs uvicorn with 2
# workers (no --reload), and HEALTHCHECK hits /api/health.
# ---------------------------------------------------------------------------

# ---------- builder ----------
FROM python:3.12-slim AS builder

# Install uv from the official distroless image. Pinned to :latest because
# uv guarantees a stable CLI; reproducibility is achieved via uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.12

WORKDIR /build

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/

# Compile bytecode so the runtime image starts faster.
RUN uv sync --no-dev --compile-bytecode

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    SLIDEATELIER_ENV=production

# Non-root user. UID 10001 is the convention for unprivileged service users.
RUN groupadd --system --gid 10001 slideatelier \
 && useradd --system --uid 10001 --gid slideatelier \
            --home /app --shell /usr/sbin/nologin slideatelier

# Minimal runtime deps. curl is needed for HEALTHCHECK.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the prebuilt venv + source from the builder. No build tools land here.
COPY --from=builder --chown=slideatelier:slideatelier /build/.venv /app/.venv
COPY --chown=slideatelier:slideatelier src/ /app/src/
COPY --chown=slideatelier:slideatelier pyproject.toml /app/pyproject.toml

# Output and templates dirs are mounted as volumes in production but we
# create them so a vanilla `docker run` works too.
RUN mkdir -p /app/output /app/templates /app/library \
 && chown -R slideatelier:slideatelier /app

USER slideatelier

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent --show-error http://127.0.0.1:8000/api/health || exit 1

# Two workers — enough for indie launch traffic, low memory ceiling.
# --proxy-headers + --forwarded-allow-ips=* lets uvicorn trust Caddy's
# X-Forwarded-* headers so url_for() and redirects use https.
CMD ["uvicorn", "slideatelier.web.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
