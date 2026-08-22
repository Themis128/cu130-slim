FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and apply OS security patches.
# apt-get upgrade fixes OS-level CVEs: perl (#641,#639,#638,#644,#645,#643),
# util-linux/libblkid (#664,#663,#662,#661)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and wheel to latest versions (security fixes for pip #683 and wheel #704)
RUN pip install --no-cache-dir --upgrade pip wheel

# Copy dependency files
COPY pyproject.toml .

# Install Python dependencies (upgraded versions fix starlette, python-multipart,
# python-jose, cryptography, ecdsa, jaraco.context vulnerabilities)
RUN pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Run migrations and start
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
