FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and apply OS security patches.
# apt-get upgrade fixes OS-level CVEs: perl (#641,#639,#638,#644,#645,#643),
# openssl (#633,#631,#579,#577), ncurses (#629,#627,#588,#499), acl (#192),
# gzip (#190), util-linux/libblkid (#664,#663,#662,#661)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip, wheel, setuptools, and msgpack to patched versions.
# Security fixes: wheel 0.46.2 (CVE-2026-24049), setuptools 78.1.1 (CVE-2025-47273),
# msgpack 1.2.1 (CVE-2026-57585), jaraco.context 6.1.0 (CVE-2026-23949)
RUN pip install --no-cache-dir --upgrade "pip>=25.0" "wheel>=0.46.2" "setuptools>=78.1.1" "msgpack>=1.2.1" "jaraco.context>=6.1.0"

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies (upgraded versions fix starlette, python-multipart,
# python-jose, cryptography, ecdsa, jaraco.context vulnerabilities)
RUN pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Run migrations and start
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
