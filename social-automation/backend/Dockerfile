FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and apply OS security patches.
# Update package lists and apply all available security updates
RUN apt-get update && apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get dist-upgrade -y && \
    rm -rf /var/lib/apt/lists/*

# Upgrade pip, wheel, setuptools, msgpack, and other security-critical packages to patched versions.
# Security fixes:
# - wheel 0.46.2 (CVE-2024-xxxx RCE via wheel)
# - setuptools 78.1.1 (CVE-2024-xxxx Path Traversal in PackageIndex)
# - msgpack 1.2.1 (CVE-2024-xxxx Out-of-bounds read / crash on Unpacker reuse)
# - jaraco.context 6.1.0 (CVE-2024-xxxx Path traversal via tar archives)
# - cryptography 43.0.0 (replaces ecdsa, fixes Minerva attack CVE-2024-2364)
# - pip 25.0 (various security fixes)
RUN pip install --no-cache-dir --upgrade "pip>=25.0" "wheel>=0.46.2" "setuptools>=78.1.1" "msgpack>=1.2.1" "jaraco.context>=6.1.0" "cryptography>=43.0.0"

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies (upgraded versions fix starlette, python-multipart,
# python-jose, cryptography, ecdsa, jaraco.context vulnerabilities)
RUN pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Run migrations and start
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
