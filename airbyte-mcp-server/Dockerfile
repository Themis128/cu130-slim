FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Install airbyte-mcp + dependencies
RUN uv pip install --system --no-cache-dir \
    "airbyte-mcp>=1.0.0" \
    "mcp>=1.0" \
    "fastmcp>=2.0" \
    "httpx>=0.27"

# Create project directory
RUN mkdir -p /data /app

# Copy the wrapper script
COPY server.py /app/server.py

WORKDIR /app

ENV AIRBYTE_PROJECT_DIR=/data \
    AIRBYTE_MCP_SAFE_MODE=1 \
    AIRBYTE_CLOUD_MCP_READONLY_MODE=0 \
    PYTHONUNBUFFERED=1

EXPOSE 9228 9229

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=60s \
    CMD curl -sf http://127.0.0.1:9229/health || exit 1

CMD ["python3", "/app/server.py"]
