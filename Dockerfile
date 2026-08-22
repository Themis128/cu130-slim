# ComfyUI - GPU-Accelerated image for WSL2 / host port 8000
# NOTE: 'yanwk/comfyui:latest' (from the original task) was REMOVED from
# Docker Hub, so this deterministic image builds the official ComfyUI source.
# Uses CUDA PyTorch for NVIDIA GPU acceleration (RTX 3070 / 8GB VRAM).

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# System deps: git (custom nodes), ffmpeg (video nodes), libgl (PIL/numpy)
# apt-get upgrade applies latest OS security patches for perl, util-linux, libblkid
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git ffmpeg libgl1 libglib2.0-0 curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# App code lives OUTSIDE the data mount so the mounted storage stays clean
WORKDIR /opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git .

# Python deps - CUDA PyTorch 2.6, then ComfyUI requirements
# comfy-kitchen 0.1.6 works with PyTorch 2.6 (newer versions have type hint issues with torch.custom_ops)
# Increased timeout and retries for PyTorch download due to network issues
RUN pip install --no-cache-dir --retries 20 --timeout 1200 \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && pip install --no-cache-dir --retries 5 --timeout 120 "comfy-kitchen==0.1.6" \
    && grep -v "comfy-kitchen" requirements.txt | pip install --no-cache-dir --retries 5 --timeout 120 -r /dev/stdin \
    && pip install --no-cache-dir --retries 5 --timeout 120 replicate \
    && pip install --no-cache-dir --retries 5 --timeout 120 natsort decord

# Patch attention.py to handle missing int8_attention_is_available in older comfy_kitchen versions
RUN sed -i 's/COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = comfy_kitchen.int8_attention_is_available()/COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = getattr(comfy_kitchen, "int8_attention_is_available", lambda: False)()/' /opt/ComfyUI/comfy/ldm/modules/attention.py

# soundfile is required by custom_nodes/ComfyUI-Replicate (node.py imports it)
RUN pip install --no-cache-dir --retries 5 --timeout 60 soundfile

# Data-directory infrastructure:
# compose bind-mounts ./storage onto /home/user/ComfyUI; symlinks point the
# ComfyUI workdir at that mount so models/output/input/custom_nodes persist
# on the host.
# Note: symlinks must be created at runtime since /home/user/ComfyUI is a bind mount
RUN mkdir -p /home/user/ComfyUI/{custom_nodes,models,output,input} \
    && rm -rf /opt/ComfyUI/custom_nodes /opt/ComfyUI/models /opt/ComfyUI/output /opt/ComfyUI/input

# Create entrypoint script to setup symlinks at runtime
RUN echo '#!/bin/bash\nmkdir -p /home/user/ComfyUI/{custom_nodes,models,output,input}\nln -sfn /home/user/ComfyUI/models /opt/ComfyUI/models\nln -sfn /home/user/ComfyUI/output /opt/ComfyUI/output\nln -sfn /home/user/ComfyUI/input /opt/ComfyUI/input\nln -sfn /home/user/ComfyUI/custom_nodes /opt/ComfyUI/custom_nodes\npython3 main.py ${CLI_ARGS:---listen 0.0.0.0 --port 8000}' > /entrypoint.sh \
    && chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# CLI_ARGS is provided by docker-compose: " --listen 0.0.0.0 --port 8000"
ENTRYPOINT ["/entrypoint.sh"]
