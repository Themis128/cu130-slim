#!/usr/bin/env bash
# Generate a screencast MP4 from a folder of PNG screenshots using ffmpeg.
# Runs inside the social-api container (has ffmpeg installed).
# Usage: bash generate-screencast.sh [frames_dir] [output_path] [framerate]
set -euo pipefail

FRAMES_DIR="${1:-/tmp/screencast-frames}"
OUTPUT_PATH="${2:-/tmp/screencast-output/cloudless-screencast.mp4}"
FRAMERATE="${3:-5}"

echo "Generating screencast from $FRAMES_DIR → $OUTPUT_PATH"
echo "Framerate: $FRAMERATE fps"
echo ""

# Ensure output directory exists
mkdir -p "$(dirname "$OUTPUT_PATH")"

# Check if frames exist
FRAME_COUNT=$(ls "$FRAMES_DIR"/frame_*.png 2>/dev/null | wc -l || echo "0")
if [ "$FRAME_COUNT" -eq 0 ]; then
  echo "Error: No frame_*.png files found in $FRAMES_DIR"
  echo "Expected files: frame_00.png, frame_01.png, ..."
  exit 1
fi

echo "Found $FRAME_COUNT frames"

# Generate the MP4
docker compose exec -T social-api ffmpeg -y \
  -framerate "$FRAMERATE" \
  -i "/tmp/screencast-frames/frame_%02d.png" \
  -c:v libx264 \
  -preset slow \
  -crf 20 \
  -pix_fmt yuv420p \
  -s 1280x720 \
  "/tmp/screencast-output/cloudless-screencast.mp4"

# Copy back to host
docker compose cp social-api:/tmp/screencast-output/cloudless-screencast.mp4 "$OUTPUT_PATH"

echo ""
echo "Screencast generated: $OUTPUT_PATH"
echo "Size: $(du -h "$OUTPUT_PATH" | cut -f1)"
echo "Duration: ~$(echo "scale=1; $FRAME_COUNT / $FRAMERATE" | bc) seconds"
