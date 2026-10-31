# syntax=docker/dockerfile:1

# ── Builder: compile/install deps with a toolchain that never ships ──
FROM mirror.gcr.io/library/python:3.11-slim AS builder

WORKDIR /app

# Build-time only: gcc/headers compile wheels that lack prebuilt binaries.
# None of these land in the runtime image (the venv is copied out whole).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Security fixes (see comment block below on the runtime stage).
RUN pip install --no-cache-dir --upgrade "pip>=25.0" "wheel>=0.46.2" "setuptools>=78.1.1"

# Install Python dependencies into the venv. Upgraded versions fix starlette,
# python-multipart, python-jose, cryptography, jaraco.context vulnerabilities;
# the trailing upgrade re-pins them in case a dep dragged an older release.
# The worker extra pulls the media/PDF/notebook stack (~1GB) that only the
# celery images need — the api target builds its own slim venv below.
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[worker]" && \
    pip install --no-cache-dir --upgrade "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0"

# instagrapi is installed --no-deps: every release >=2.6.9 (the
# GHSA-ggxf-37hm-9wqf fix) hard-pins Pillow<13, conflicting with the
# Pillow>=13.0.0 security floor. Its remaining runtime deps (requests,
# pydantic, PySocks, pycryptodomex) are resolved explicitly here.
RUN pip install --no-cache-dir "instagrapi==2.6.9" --no-deps && \
    pip install --no-cache-dir "PySocks>=1.7.1" "pycryptodomex>=3.23.0"

# ── Runtime layers shared by the api and every worker — everything except
# the venv, which each target copies in from its own builder stage. ──
# No compilers, no browser engines — browser automation lives in the
# dedicated sidecar containers, not in this codebase.
FROM mirror.gcr.io/library/python:3.11-slim AS runtime-common

WORKDIR /app

# Runtime system deps + OS security patches.
RUN apt-get update && apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
    openssl \
    poppler-utils \
    ffmpeg \
    && apt-get full-upgrade -y && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Upgrade the interpreter's own packaging tools too — Trivy scans the base
# interpreter, not just the venv:
# - wheel 0.46.2 (RCE via wheel)
# - setuptools 78.1.1 (Path Traversal in PackageIndex)
# - msgpack 1.2.1 (Out-of-bounds read / crash on Unpacker reuse)
# - jaraco.context 6.1.0 (Path traversal via tar archives)
# - cryptography 43.0.0 (fixes Minerva attack CVE-2024-2364)
# - pip 25.0 (various security fixes)
RUN /usr/local/bin/pip install --no-cache-dir --upgrade "pip>=25.0" "wheel>=0.46.2" "setuptools>=78.1.1" "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0"

# Copy application code
COPY . .
COPY --from=browser browser-bridge.py /app/browser-bridge.py

# Run as non-root user
RUN addgroup --gid 1001 app \
    && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" app \
    && chown -R app:app /app
USER app
ENV PATH="/opt/venv/bin:$PATH"

# ── Slim api builder: core deps only (no worker media/PDF/notebook stack) ──
FROM builder AS builder-api
RUN python -m venv /opt/venv-api && \
    /opt/venv-api/bin/pip install --no-cache-dir -e . && \
    /opt/venv-api/bin/pip install --no-cache-dir "instagrapi==2.6.9" --no-deps && \
    /opt/venv-api/bin/pip install --no-cache-dir --upgrade "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0" && \
    find /opt/venv-api/lib/python*/site-packages \
    -type f \( -name "*.cdx.json" -o -name "*.spdx.json" \) -delete 2>/dev/null || true

# ── Targets ──
# `base` = full worker image (all deps incl. the worker extra).
FROM runtime-common AS base
COPY --from=builder /opt/venv /opt/venv
# Remove bundled SBOM files that can confuse Trivy with stale package versions.
USER root
RUN find /opt/venv/lib/python*/site-packages \
    -type f \( -name "*.cdx.json" -o -name "*.spdx.json" \) -delete 2>/dev/null || true
USER app

# `api` = slim image — core venv only; worker-extra import sites are all
# function-lazy or ImportError-guarded.
FROM runtime-common AS api
COPY --from=builder-api /opt/venv-api /opt/venv
CMD ["sh", "-c", "alembic upgrade head && (python scripts/seed_content_templates.py || echo 'WARN content-template seed failed (non-fatal)') && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]

FROM base AS worker
# Worker only — beat scheduler runs in the dedicated celery-beat service
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
