#!/usr/bin/env bash
# Build a slideshow video from image media assets in the SocialAuto library.
# Downloads each asset via the media/view endpoint, builds an MP4 with ffmpeg
# in the comfyui container, and outputs the path to the resulting video.
#
# Usage: build-slideshow.sh <asset-id-1> [<asset-id-2> ...] [--seconds 3] [--output /tmp/video.mp4]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)
TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

SECONDS_PER_SLIDE=3
OUTPUT="/tmp/tiktok-slideshow.mp4"
ASSET_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds) SECONDS_PER_SLIDE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    -*) echo "Unknown arg: $1" >&2; exit 2 ;;
    *) ASSET_IDS+=("$1"); shift ;;
  esac
done

if [ ${#ASSET_IDS[@]} -eq 0 ]; then
  echo "Usage: build-slideshow.sh <asset-id-1> [<asset-id-2> ...] [--seconds 3] [--output /tmp/video.mp4]"
  exit 1
fi

# Prepare comfyui input dir
INPUT_DIR="/home/tbaltzakis/cu130-slim/storage-user/input/tiktok-slides"
mkdir -p "$INPUT_DIR"
rm -f "$INPUT_DIR"/slide-*.png

echo "Downloading ${#ASSET_IDS[@]} assets..."
i=1
for AID in "${ASSET_IDS[@]}"; do
  # Get asset storage_path
  STORAGE_PATH=$(curl -sf -H "Authorization: Bearer $TOKEN" "$API/api/v1/media/assets/$AID" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('storage_path',''))")
  if [ -z "$STORAGE_PATH" ]; then
    echo "  Asset $AID: not found, skipping"
    continue
  fi
  # Try public URL first, fall back to MinIO via social-api
  URL="https://social.cloudless.gr/api/v1/media/view?path=$STORAGE_PATH"
  HTTP_CODE=$(curl -s -o "$INPUT_DIR/slide-$i.png" -w "%{http_code}" "$URL")
  if [ "$HTTP_CODE" != "200" ] || [ "$(stat -c%s "$INPUT_DIR/slide-$i.png" 2>/dev/null || echo 0)" -lt 100 ]; then
    echo "  Asset $AID: fetching from MinIO..."
    docker compose exec -T social-api python3 -c "
import asyncio
from app.services import minio_storage
async def main():
    data = await minio_storage.get_object('$STORAGE_PATH')
    with open('/tmp/slide-$i.png','wb') as f:
        f.write(data)
    print(f'  Downloaded {len(data)} bytes')
asyncio.run(main())
" 2>&1
    docker compose cp social-api:/tmp/slide-$i.png "$INPUT_DIR/slide-$i.png"
  fi
  echo "  slide-$i.png ← $AID ($STORAGE_PATH)"
  i=$((i + 1))
done

NUM_SLIDES=$((i - 1))
if [ "$NUM_SLIDES" -lt 1 ]; then
  echo "No slides downloaded. Aborting."
  exit 1
fi

echo "Building slideshow: $NUM_SLIDES slides, ${SECONDS_PER_SLIDE}s each, 1080x1080..."

# Build concat list and run ffmpeg in comfyui container
docker exec social-media-comfyui-gpu bash -c "
cd /home/user/ComfyUI/input/tiktok-slides
LIST=/tmp/slideshow.txt
> \$LIST
for n in \$(seq 1 $NUM_SLIDES); do
  echo \"file '/home/user/ComfyUI/input/tiktok-slides/slide-\${n}.png'\" >> \$LIST
  echo \"duration $SECONDS_PER_SLIDE\" >> \$LIST
done
# Repeat last frame (concat demuxer requirement)
echo \"file '/home/user/ComfyUI/input/tiktok-slides/slide-${NUM_SLIDES}.png'\" >> \$LIST

ffmpeg -y -f concat -safe 0 -i \$LIST \\
  -vf 'scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p' \\
  -r 30 -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \\
  /home/user/ComfyUI/output/tiktok-slideshow.mp4 2>&1 | tail -5
"

# Copy to host
OUTPUT_DIR="/home/tbaltzakis/cu130-slim/storage-user/output"
cp "$OUTPUT_DIR/tiktok-slideshow.mp4" "$OUTPUT"
SIZE=$(stat -c%s "$OUTPUT" 2>/dev/null || stat -f%z "$OUTPUT" 2>/dev/null || echo 0)
DURATION=$(docker exec social-media-comfyui-gpu ffprobe -v quiet -show_entries format=duration -of csv=p=0 /home/user/ComfyUI/output/tiktok-slideshow.mp4 2>/dev/null | head -1)

echo ""
echo "Slideshow built: $OUTPUT"
echo "  Slides: $NUM_SLIDES"
echo "  Duration: ${DURATION}s"
echo "  Size: $((SIZE / 1024))KB"
echo ""
echo "Upload to media library:"
echo "  curl -sf -X POST '$API/api/v1/media/upload' \\"
echo "    -H 'Authorization: Bearer $TOKEN' \\"
echo "    -F 'file=@$OUTPUT' \\"
echo "    -F 'tags=tiktok,slideshow'"
