# ComfyUI - GPU-Accelerated image for WSL2 / host port 8000
# NOTE: 'yanwk/comfyui:latest' (from the original task) was REMOVED from
# Docker Hub, so this deterministic image builds the official ComfyUI source.
# Uses CUDA PyTorch for NVIDIA GPU acceleration (RTX 3070 / 8GB VRAM).

# Updated base image with security patches (CUDA 13.0 + Ubuntu 22.04 with latest patches)
FROM nvidia/cuda:13.0.1-cudnn-devel-ubuntu22.04

# System deps: git (custom nodes), ffmpeg (video nodes), libgl (PIL/numpy)
# apt-get upgrade applies latest OS security patches for perl, openssl, ncurses, libacl, gzip, util-linux, libblkid
# Critical vulnerabilities fixed: CVE-2024-xxxx in perl, openssl, ncurses, acl, gzip, perl-Archive-Tar
# Remove NVIDIA Nsight Compute (1.3GB) which contains vulnerable Go binary (nic_sampler)
# with multiple CVEs: CVE-2024-xxxx in crypto/tls, net/url, encoding/xml, html/template, net/http, mime, net/mail, net, golang.org/x/net/idna, encoding/asn1, crypto/x509
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev git ffmpeg libgl1 libglib2.0-0 curl build-essential \
        libncursesw6 \
        libssl-dev \
        libtinfo6 \
        openssl \
        perl-base \
    && apt-get dist-upgrade -y \
    && rm -rf /opt/nvidia/nsight-compute \
    && rm -rf /var/lib/apt/lists/*

# App code lives OUTSIDE the data mount so the mounted storage stays clean
WORKDIR /opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git .

# Security-pinned versions addressing Trivy findings:
# - starlette>=0.52.2 (CVE-2024-xxxx DoS/SSRF)
# - setuptools>=78.1.1 (CVE-2025-47273 Path traversal in PackageIndex)
# - msgpack>=1.0.8 (CVE-2024-xxxx OOB read)
# - wheel>=0.46.2 (CVE-2026-24049 RCE via malicious wheel file)
# - jaraco.context>=6.1.0 (CVE-2026-23949 Path traversal via tar archives)
# - cryptography>=43.0.0 (recommended replacement for ecdsa; Minerva attack fixed)
# NOTE: torchvision MUST be pinned. Unpinned, a later PyPI install (decord,
# replicate, ...) resolves torchvision to the ancient universal 0.1.6 wheel
# (the cu130 index has no bare 'torchvision' for cp310), which imports 'six'
# and crashes ComfyUI at startup. Also, torchvision 0.28.0+cu130 from cu130
# index lacks torchvision.ops - we need to use torchvision 0.18.0+cu118
# which is compatible with torch 2.13 and includes the ops module.
# Install PyTorch packages from cu118 index
# torchvision 0.20.0+cu118 is compatible with torch >=2.4 (which has torch.library.custom_op)
# nvidia-cublas and related CUDA wheels are very large (several GB); use a generous
# timeout and retry count to survive transient network hiccups during download.
RUN pip install --no-cache-dir --retries 50 --timeout 7200 \
    "torchvision==0.20.0+cu118" torchaudio --index-url https://download.pytorch.org/whl/cu118 \
    && pip install --no-cache-dir --retries 10 --timeout 300 "comfy-kitchen==0.2.31" \
    && sed -i '1i from typing import List' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/kernel_size: list\[int\]/kernel_size: List[int]/g' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/is_causal: list\[bool\]/is_causal: List[bool]/g' /usr/local/lib/python3.10/dist-packages/comfy_kitchen/backends/eager/na.py \
    && grep -v "comfy-kitchen" requirements.txt | grep -v "^torch$" | grep -v "^torchvision$" | grep -v "^torchaudio$" | pip install --no-cache-dir --retries 10 --timeout 300 -r /dev/stdin \
    && pip install --no-cache-dir --retries 10 --timeout 300 \
        replicate \
        natsort \
        decord \
        six \
        "starlette>=0.52.2" \
        "setuptools>=78.1.1" \
        "msgpack>=1.0.8" \
        "wheel>=0.46.2" \
        "jaraco.context>=6.1.0" \
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