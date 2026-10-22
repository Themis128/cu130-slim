# ComfyUI - GPU-Accelerated image for WSL2 / host port 8000
# NOTE: 'yanwk/comfyui:latest' (from the original task) was REMOVED from
# Docker Hub, so this deterministic image builds the official ComfyUI source.
# Uses CUDA PyTorch for NVIDIA GPU acceleration (RTX 3070 / 8GB VRAM).

# ──────────────────────────────────────────────────────────────────────────
# Stage 1 — py-builder: compile all Python deps into a self-contained venv.
# The build toolchain (build-essential, *-dev headers) lives ONLY here —
# it never reaches the runtime image.
# ──────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04 AS py-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev \
        git build-essential libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --copies embeds the interpreter binary so the venv stays valid in the
# runtime stage regardless of the base image's exact python3.10 point release.
RUN python3 -m venv --copies /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ensurepip bundles Ubuntu's ancient pip 22.0.2 into the venv — Trivy flags it
# (CVE-2023-5752, CVE-2025-8869, CVE-2026-13346, ...). Upgrade before anything else.
# urllib3 2.6.2-2.7.0 carries chunked-deflate/chunk-size DoS CVEs
# (97687/97688/97689) — requests resolves 2.7.0 transitively, pin the floor.
RUN pip install --no-cache-dir --upgrade pip "urllib3>=2.8.0"

# ComfyUI source — cloned in the builder only for requirements.txt; the
# runtime stage clones its own copy under /opt/ComfyUI.
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /tmp/comfy

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
# torchvision 0.21.0+cu118 pulls torch 2.6.0+cu118 (fixes CVE-2025-32434 arbitrary code
# execution via torch.load with malicious checkpoint).  0.21.0 still requires
# torch.library.custom_op (added 2.4) and is compatible with comfy-kitchen 0.2.x.
# nvidia-cublas and related CUDA wheels are very large (several GB); use a generous
# timeout and retry count to survive transient network hiccups during download.
# Layer 1 (stable, large): PyTorch family — cached independently so volatile
# security-pin bumps below don't invalidate this multi-GB layer.
RUN pip install --no-cache-dir --retries 50 --timeout 7200 \
    "torchvision==0.21.0+cu118" torchaudio --index-url https://download.pytorch.org/whl/cu118

# Layer 2: ComfyUI requirements + comfy-kitchen patches
RUN pip install --no-cache-dir --retries 10 --timeout 300 "comfy-kitchen==0.2.31" \
    && SITE=/opt/venv/lib/python3.10/site-packages \
    && sed -i '1i from typing import List' $SITE/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/kernel_size: list\[int\]/kernel_size: List[int]/g' $SITE/comfy_kitchen/backends/eager/na.py \
    && sed -i 's/is_causal: list\[bool\]/is_causal: List[bool]/g' $SITE/comfy_kitchen/backends/eager/na.py \
    && grep -v "comfy-kitchen" /tmp/comfy/requirements.txt | grep -v "^torch$" | grep -v "^torchvision$" | grep -v "^torchaudio$" | pip install --no-cache-dir --retries 10 --timeout 300 -r /dev/stdin

# Layer 3: Security-pinned extras — small, changes more often than torch
RUN pip install --no-cache-dir --retries 10 --timeout 300 \
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

# soundfile is required by custom_nodes/ComfyUI-Replicate (node.py imports it)
RUN pip install --no-cache-dir --retries 5 --timeout 60 soundfile

# Layer 4: custom-node dependencies. custom_nodes/ is bind-mounted at runtime
# (storage-user/custom_nodes), so their requirements.txt files can't be read
# at build time — pin the union here instead. Sources:
#   ComfyUI-GGUF:            gguf, sentencepiece, protobuf
#   ComfyUI-LTXVideo:        colour-science, diffusers, einops, huggingface_hub,
#                            kornia, ninja, openimageio, transformers[timm]
#   ComfyUI-VideoHelperSuite: opencv-python (headless variant — no GUI libs),
#                            imageio-ffmpeg
RUN pip install --no-cache-dir --retries 10 --timeout 300 \
        "gguf>=0.13.0" \
        sentencepiece \
        protobuf \
        "colour-science>=0.4.4" \
        diffusers \
        einops \
        "huggingface_hub>=0.25.2" \
        kornia \
        "ninja~=1.11.1.4" \
        "transformers[timm]>=4.50.0" \
        opencv-python-headless \
        imageio-ffmpeg

# NOTE: triton JIT-compiles a C stub per kernel signature at RUNTIME
# (triton/backends/nvidia/driver.py make_launcher → compile_module_from_src
# → gcc). Cache-warming can't cover arbitrary kernels, so the runtime
# stage keeps a minimal C toolchain (gcc + python3-dev headers) instead
# of the full build-essential it had before.


# ──────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime: no CUDA toolkit in the image at all.
# The torch cu118 wheels bundle every CUDA/cuDNN/NCCL lib they need; the
# NVIDIA driver is injected at run time by `runtime: nvidia` in compose.
# Dropping nvcr.io/nvidia/cuda saves ~3.1GB; keeping only gcc+python3-dev
# (not build-essential/libssl-dev — see triton note above) trims ~0.5GB.
# ──────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# System deps: git (custom nodes), ffmpeg (video nodes), libgl (PIL/numpy),
# libgomp1 (torch OpenMP runtime), ca-certificates (git clone https).
# apt-get dist-upgrade applies latest OS security patches (perl, openssl,
# ncurses, acl, gzip, util-linux, libblkid).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev gcc git ffmpeg libgl1 libglib2.0-0 libgomp1 \
        curl ca-certificates \
        libncursesw6 \
        libtinfo6 \
        openssl \
        perl-base \
    && apt-get dist-upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Python environment — copied whole from the builder stage.
COPY --from=py-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# App code lives OUTSIDE the data mount so the mounted storage stays clean
WORKDIR /opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git .

# Patch attention.py to handle missing int8_attention_is_available in older comfy_kitchen versions
RUN sed -i 's/COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = comfy_kitchen.int8_attention_is_available()/COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = getattr(comfy_kitchen, "int8_attention_is_available", lambda: False)()/' /opt/ComfyUI/comfy/ldm/modules/attention.py

# Data-directory infrastructure:
# compose bind-mounts ./storage onto /home/user/ComfyUI; symlinks point the
# ComfyUI workdir at that mount so models/output/input/custom_nodes persist
# on the host.
# Note: symlinks must be created at runtime since /home/user/ComfyUI is a bind mount
RUN mkdir -p /home/user/ComfyUI/{custom_nodes,models,output,input} \
    && rm -rf /opt/ComfyUI/custom_nodes /opt/ComfyUI/models /opt/ComfyUI/output /opt/ComfyUI/input

# Create entrypoint script to setup symlinks at runtime.
# Uses printf (not echo) so \n is reliably interpreted; exec replaces bash
# with python3 so the process becomes PID 1 and receives SIGTERM correctly.
# Also creates libcuda.so symlink for triton JIT compilation (WSL provides libcuda.so.1.1 only).
RUN printf '#!/bin/bash\nset -e\n# Create libcuda.so symlink for triton JIT compilation\nfor d in /usr/lib/wsl/drivers/nvltsi.inf_amd64_*/; do\n  if [ -f "$d/libcuda.so.1.1" ] && [ ! -f "$d/libcuda.so" ]; then\n    ln -sf libcuda.so.1.1 "$d/libcuda.so"\n    echo "Created libcuda.so symlink in $d"\n  fi\ndone\nmkdir -p /home/user/ComfyUI/{custom_nodes,models,output,input}\nln -sfn /home/user/ComfyUI/models /opt/ComfyUI/models\nln -sfn /home/user/ComfyUI/output /opt/ComfyUI/output\nln -sfn /home/user/ComfyUI/input /opt/ComfyUI/input\nln -sfn /home/user/ComfyUI/custom_nodes /opt/ComfyUI/custom_nodes\n\nPROFILE=${COMFYUI_PROFILE:-sdxl}\nPROFILE_FILE="/opt/ComfyUI/profiles/${PROFILE}.txt"\nPROFILE_ARGS=""\nif [ -f "$PROFILE_FILE" ]; then\n  PROFILE_ARGS=$(grep -v "^#" "$PROFILE_FILE" | tr "\\n" " ")\nfi\n\nBASE_ARGS="--listen 0.0.0.0 --port 8000 --preview-method auto"\nexec python3 main.py $BASE_ARGS $PROFILE_ARGS ${CLI_ARGS:-}\n' > /entrypoint.sh \
    && chmod +x /entrypoint.sh

EXPOSE 8000

# CLI_ARGS is provided by docker-compose: " --listen 0.0.0.0 --port 8000"
ENTRYPOINT ["/entrypoint.sh"]
