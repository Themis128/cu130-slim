# Lightweight Diffusers text-to-image server for NVIDIA GPU (RTX 3070 / 8GB VRAM)
# Exposes OpenAI-compatible /v1/images/generations API on port 7860
FROM nvidia/cuda:12.6.0-cudnn-runtime-ubuntu22.04

# System deps: python3.11, pip, curl (healthcheck), libgl (PIL/numpy)
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3-pip curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python3.11 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install Python deps (layer cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server
COPY server.py .

# Model cache volume
ENV HF_HOME=/models/huggingface
ENV TRANSFORMERS_CACHE=/models/huggingface
ENV DIFFUSERS_CACHE=/models/huggingface

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --retries=10 --start-period=180s \
    CMD curl -sf http://localhost:7860/health || exit 1

CMD ["python3", "server.py"]
