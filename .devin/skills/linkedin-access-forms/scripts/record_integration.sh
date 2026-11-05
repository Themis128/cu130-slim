#!/usr/bin/env bash
# Record a real screen capture of the SocialAuto LinkedIn integration for the
# LinkedIn access-request form's "screen recording" upload. Captures sequential
# screenshots of the live SocialAuto UI (accounts -> LinkedIn -> posts ->
# publish flow) inside social-api's ffmpeg into an mp4.
#
# The win_agent /screenshot lane captures the user's REAL Chrome already on the
# surfaces — no separate browser needed. Stitched at 1fps for a compact mp4.
#
# Usage:
#   ./record_integration.sh [--frames N] [--fps N] [--out /tmp/li-integration.mp4]
#
# Requires: win_agent on :8765 (see win-bridge-ops), ffmpeg inside social-api.

set -euo pipefail

AGENT="${AGENT:-http://172.22.144.1:8765}"
FRAMES="${2:-30}"
FPS="${4:-1}"
OUT="${6:-/tmp/li-integration.mp4}"
DIR="/tmp/li-rec-$$"
mkdir -p "$DIR"

echo "[rec] capturing $FRAMES frames from $AGENT/screenshot ..."
for i in $(seq 1 "$FRAMES"); do
  curl -s --max-time 15 "$AGENT/screenshot" -o "$DIR/frame_$(printf '%03d' "$i").png" || true
  sleep 1
done

echo "[rec] stitching with ffmpeg inside social-api ..."
docker compose cp "$DIR" social-api:/tmp/li-rec-$$ >/dev/null
docker compose exec -T social-api ffmpeg -y -framerate "$FPS" \
  -i "/tmp/li-rec-$$/frame_%03d.png" -vf "scale=1280:-2,format=yuv420p" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT" >/dev/null 2>&1

docker compose cp "social-api:$OUT" "$OUT" 2>/dev/null || true
rm -rf "$DIR"
echo "[rec] done -> $OUT"
