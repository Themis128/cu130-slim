#!/usr/bin/env python3
"""Switch the active ComfyUI VRAM profile in docker-compose.yml.
Usage: comfyui-switch-profile.py [sdxl|flux|quality]"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
profile = sys.argv[1] if len(sys.argv) > 1 else "sdxl"

if not (ROOT / f"comfyui/profiles/{profile}.txt").is_file():
    print(f"Unknown profile: {profile}", file=sys.stderr)
    print("Available: sdxl, flux, quality", file=sys.stderr)
    sys.exit(1)

compose = ROOT / "docker-compose.yml"
text = compose.read_text()
if not re.search(r"^\s*- COMFYUI_PROFILE=", text, re.MULTILINE):
    print("COMFYUI_PROFILE not found in docker-compose.yml", file=sys.stderr)
    sys.exit(1)

text = re.sub(r"^(\s*- COMFYUI_PROFILE=).*", rf"\g<1>{profile}",
              text, flags=re.MULTILINE)
compose.write_text(text)
print(f"Switched ComfyUI profile to: {profile}")
print("Restart ComfyUI to apply: docker compose restart comfyui")
