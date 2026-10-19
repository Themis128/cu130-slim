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
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir --upgrade "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0"

# ── Runtime base shared by the API and every worker ──
# No compilers, no browser engines — browser automation lives in the
# dedicated sidecar containers, not in this codebase.
FROM mirror.gcr.io/library/python:3.11-slim AS base

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

# Application venv from the builder (all deps, no toolchain).
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade the interpreter's own packaging tools too — Trivy scans the base
# interpreter, not just the venv:
# - wheel 0.46.2 (RCE via wheel)
# - setuptools 78.1.1 (Path Traversal in PackageIndex)
# - msgpack 1.2.1 (Out-of-bounds read / crash on Unpacker reuse)
# - jaraco.context 6.1.0 (Path traversal via tar archives)
# - cryptography 43.0.0 (fixes Minerva attack CVE-2024-2364)
# - pip 25.0 (various security fixes)
RUN /usr/local/bin/pip install --no-cache-dir --upgrade "pip>=25.0" "wheel>=0.46.2" "setuptools>=78.1.1" "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0"

# Remove bundled SBOM files that can confuse Trivy with stale package versions.
RUN find /opt/venv/lib/python*/site-packages /usr/local/lib/python*/site-packages \
    -type f \( -name "*.cdx.json" -o -name "*.spdx.json" \) -delete 2>/dev/null || true

# Copy application code
COPY . .

# Run as non-root user
RUN addgroup --gid 1001 app \
    && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" app \
    && chown -R app:app /app
USER app

# ── Targets ──
FROM base AS api
CMD ["sh", "-c", "alembic upgrade head && (python scripts/seed_content_templates.py || echo 'WARN content-template seed failed (non-fatal)') && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]

FROM base AS worker
# Worker only — beat scheduler runs in the dedicated celery-beat service
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
