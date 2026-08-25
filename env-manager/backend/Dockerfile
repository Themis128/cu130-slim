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

# Upgrade pip and wheel to latest versions (security fixes)
RUN pip install --no-cache-dir --upgrade pip wheel

# Copy dependency files
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

EXPOSE 8081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
