#!/usr/bin/env bash
# Switch the active ComfyUI VRAM profile in docker-compose.yml.
# Usage: ./scripts/comfyui-switch-profile.sh [sdxl|flux|quality]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILE="${1:-sdxl}"
FILE="comfyui/profiles/${PROFILE}.txt"

if [ ! -f "$FILE" ]; then
  echo "Unknown profile: $PROFILE" >&2
  echo "Available: sdxl, flux, quality" >&2
  exit 1
fi

if ! grep -qE "^\s*- COMFYUI_PROFILE=" docker-compose.yml; then
  echo "COMFYUI_PROFILE not found in docker-compose.yml" >&2
  exit 1
fi

sed -i -E "s/^([[:space:]]*- COMFYUI_PROFILE=).*/\1${PROFILE}/" docker-compose.yml
echo "Switched ComfyUI profile to: $PROFILE"
echo "Restart ComfyUI to apply: docker compose restart comfyui"
