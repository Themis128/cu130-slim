#!/usr/bin/env bash
# Update Instagram profile picture
# Usage: update-picture.sh [session_id] <image_file>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"
SID="${1:?Usage: update-picture.sh <session_id> <image_file>}"
IMG="${2:?Usage: update-picture.sh <session_id> <image_file>}"

if [[ ! -f "$IMG" ]]; then
  echo "Image file not found: $IMG"
  exit 1
fi

echo "Updating Instagram profile picture with $IMG..."

# Copy image into the sidecar container
CONTAINER_PATH="/tmp/ig-profile-pic-$(date +%s).png"
docker cp "$IMG" "instagram-private-api:$CONTAINER_PATH" 2>/dev/null

# Call the sidecar's picture update endpoint
curl -sf -X PATCH "$API/account/picture" \
  -H "X-Session-ID: $SID" \
  -H "Content-Type: multipart/form-data" \
  -F "picture=@$IMG" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Status: {d.get(\"status\", \"ok\")}')
    print(f'Profile pic URL: {str(d.get(\"profile_pic_url\", d.get(\"user\", {}).get(\"profile_pic_url\", \"N/A\")))[:80]}...')
except:
    print(sys.stdin.read()[:200])
"

# Cleanup
docker compose exec -T instagram-private-api rm -f "$CONTAINER_PATH" 2>/dev/null || true
