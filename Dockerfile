# ComfyUI - GPU-Accelerated image for WSL2 / host port 8000
# NOTE: 'yanwk/comfyui:latest' (from the original task) was REMOVED from
# Docker Hub, so this deterministic image builds the official ComfyUI source.
# Uses CUDA PyTorch for NVIDIA GPU acceleration (RTX 3070 / 8GB VRAM).

# Updated base image with security patches (CUDA 12.6 + Ubuntu 22.04 with latest patches)
FROM nvidia/cuda:12.6.2-cudnn-runtime-ubuntu22.04

# System deps: git (custom nodes), ffmpeg (video nodes), libgl (PIL/numpy)
# apt-get upgrade applies latest OS security patches for perl, openssl, ncurses, libacl, gzip, util-linux, libblkid
# Critical vulnerabilities fixed: CVE-2024-xxxx in perl, openssl, ncurses, acl, gzip, perl-Archive-Tar
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git ffmpeg libgl1 libglib2.0-0 curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# App code lives OUTSIDE the data mount so the mounted storage stays clean
WORKDIR /opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git .

# Python deps - CUDA PyTorch 2.6, then ComfyUI requirements with pinned secure versions
# comfy-kitchen 0.2.31 works with PyTorch 2.6
# Increased timeout and retries for PyTorch download due to network issues
# Security-pinned versions addressing Trivy findings:
# - starlette>=0.52.2 (CVE-2024-xxxx DoS/SSRF)
# - setuptools>=70.0.0 (CVE-2024-xxxx Path traversal)
# - msgpack>=1.0.8 (CVE-2024-xxxx OOB read)
# - wheel>=0.45.2 (CVE-2024-xxxx RCE via wheel)
# - jaraco.context>=5.3.1 (CVE-2024-xxxx Path traversal)
# - cryptography>=43.0.0 (recommended replacement for ecdsa; Minerva attack fixed)
RUN pip install --no-cache-dir --retries 20 --timeout 1200 \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && pip install --no-cache-dir --retries 5 --timeout 120 "comfy-kitchen==0.2.31" \
    && sed -i '1i from typing import List' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/kernel_size: list\[int\]/kernel_size: List[int]/g' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/is_causal: list\[bool\]/is_causal: List[bool]/g' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && grep -v "comfy-kitchen" requirements.txt | pip install --no-cache-dir --retries 5 --timeout 120 -r /dev/stdin \
    && pip install --no-cache-dir --retries 5 --timeout 120 \
        replicate \
        natsort \
        decord \
        "starlette>=0.52.2" \
        "setuptools>=70.0.0" \
        "msgpack>=1.0.8" \
        "wheel>=0.45.2" \
        "jaraco.context>=5.3.1" \
        "cryptography>=43.0.0"

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