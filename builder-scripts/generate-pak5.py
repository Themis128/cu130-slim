#!/usr/bin/env python3
"""Fetch requirements.txt from all ComfyUI custom-node repos and build the
deduplicated pak5.txt package list (minus entries already in pak3.txt).
Usage: generate-pak5.py"""

import re
import sys
import urllib.request
from pathlib import Path

URLS = [
    "https://github.com/Comfy-Org/ComfyUI/raw/refs/heads/master/requirements.txt",
    "https://github.com/Comfy-Org/ComfyUI-Manager/raw/refs/heads/main/requirements.txt",
    # Performance
    "https://github.com/welltop-cn/ComfyUI-TeaCache/raw/refs/heads/main/requirements.txt",
    "https://github.com/city96/ComfyUI-GGUF/raw/refs/heads/main/requirements.txt",
    # Workspace
    "https://github.com/crystian/ComfyUI-Crystools/raw/refs/heads/main/requirements.txt",
    # General
    "https://github.com/ltdrdata/was-node-suite-comfyui/raw/refs/heads/main/requirements.txt",
    "https://github.com/kijai/ComfyUI-KJNodes/raw/refs/heads/main/requirements.txt",
    "https://github.com/jags111/efficiency-nodes-comfyui/raw/refs/heads/main/requirements.txt",
    "https://github.com/yolain/ComfyUI-Easy-Use/raw/refs/heads/main/requirements.txt",
    # Control
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack/raw/refs/heads/Main/requirements.txt",
    "https://github.com/ltdrdata/ComfyUI-Impact-Subpack/raw/refs/heads/main/requirements.txt",
    "https://github.com/ltdrdata/ComfyUI-Inspire-Pack/raw/refs/heads/main/requirements.txt",
    "https://github.com/Fannovel16/comfyui_controlnet_aux/raw/refs/heads/main/requirements.txt",
    "https://github.com/Gourieff/ComfyUI-ReActor/raw/refs/heads/main/requirements.txt",
    "https://github.com/huchenlei/ComfyUI-layerdiffuse/raw/refs/heads/main/requirements.txt",
    "https://github.com/kijai/ComfyUI-Florence2/raw/refs/heads/main/requirements.txt",
    # Video
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/raw/refs/heads/main/requirements.txt",
    "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation/raw/refs/heads/main/requirements-no-cupy.txt",
    "https://github.com/melMass/comfy_mtb/raw/refs/heads/main/requirements.txt",
    "https://github.com/FizzleDorf/ComfyUI_FizzNodes/raw/refs/heads/main/requirements.txt",
    # Pending Removal — cubiq no longer maintains ComfyUI custom nodes
    "https://github.com/cubiq/ComfyUI_essentials/raw/refs/heads/main/requirements.txt",
    "https://github.com/cubiq/ComfyUI_FaceAnalysis/raw/refs/heads/main/requirements.txt",
    "https://github.com/cubiq/PuLID_ComfyUI/raw/refs/heads/main/requirements.txt",
    "https://github.com/cubiq/ComfyUI_InstantID/raw/refs/heads/main/requirements.txt",
    # Pending Removal 2 — most deps already included above
    "https://github.com/akatz-ai/ComfyUI-AKatz-Nodes/raw/refs/heads/main/requirements.txt",
    "https://github.com/akatz-ai/ComfyUI-DepthCrafter-Nodes/raw/refs/heads/main/requirements.txt",
    "https://github.com/Amorano/Jovimetrix/raw/refs/heads/main/requirements.txt",
    "https://github.com/chflame163/ComfyUI_LayerStyle/raw/refs/heads/main/repair_dependency_list.txt",
    "https://github.com/chflame163/ComfyUI_LayerStyle/raw/refs/heads/main/requirements.txt",
    "https://github.com/digitaljohn/comfyui-propost/raw/refs/heads/master/requirements.txt",
    "https://github.com/Jonseed/ComfyUI-Detail-Daemon/raw/refs/heads/main/requirements.txt",
    "https://github.com/kijai/ComfyUI-DepthAnythingV2/raw/refs/heads/main/requirements.txt",
    "https://github.com/neverbiasu/ComfyUI-SAM2/raw/refs/heads/main/requirements.txt",
    "https://github.com/pydn/ComfyUI-to-Python-Extension/raw/refs/heads/main/requirements.txt",
]

lines = []
for url in URLS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pak5"})
        lines.extend(urllib.request.urlopen(req, timeout=30)
                     .read().decode(errors="replace").splitlines())
    except Exception as e:
        print(f"warn: {url}: {e}", file=sys.stderr)

# strip comments, trailing space, version pins; normalize _ -> -
pkgs = set()
for ln in lines:
    ln = ln.split("#", 1)[0].rstrip()
    if not ln:
        continue
    ln = re.sub(r">=.*$", "", ln).strip().replace("_", "-")
    if ln:
        pkgs.add(ln.lower())

# remove items already present in pak3.txt
pak3 = Path("pak3.txt")
if pak3.exists():
    existing = {l.strip().lower() for l in pak3.read_text().splitlines()}
    pkgs -= existing

Path("pak5.txt").write_text(
    "\n".join(sorted(pkgs, key=str.casefold)) + "\n")
print("<pak5.txt> generated. Check before use.")
