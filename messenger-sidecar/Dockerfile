ARG SIDECAR_BASE=ghcr.io/themis128/cu130-slim:social-sidecar-base-latest
FROM ${SIDECAR_BASE}

WORKDIR /app

# Deps come from the shared sidecar base (fastapi/uvicorn/httpx/pydantic).
# curl for healthcheck — install as root, then back to the base's app user.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
USER app

# Copy the sidecar application
COPY app.py .

EXPOSE 9230

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9230"]
