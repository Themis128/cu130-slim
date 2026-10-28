FROM python:3.11-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the sidecar application
COPY app.py .

EXPOSE 9230

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9230"]
