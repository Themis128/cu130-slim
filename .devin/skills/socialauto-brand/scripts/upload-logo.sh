#!/usr/bin/env bash
# Upload a logo image and set it as the brand logo.
# Usage: upload-logo.sh <image_path>
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

IMAGE="${1:?Usage: upload-logo.sh <image_path>}"

if [ ! -f "$IMAGE" ]; then
  echo "File not found: $IMAGE"
  exit 1
fi

echo "=== Uploading $IMAGE to media library ==="
MEDIA_ID=$(curl -sf -X POST "$API/api/v1/media/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$IMAGE" \
  -F "alt_text=Brand logo" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')

if [ -z "$MEDIA_ID" ]; then
  echo "Upload failed."
  exit 1
fi

echo "Media ID: $MEDIA_ID"

echo ""
echo "=== Getting storage path ==="
STORAGE_PATH=$(docker compose exec -T social-postgres psql -U social_user -d social_automation -t -c \
  "SELECT storage_path FROM media_assets WHERE id = '$MEDIA_ID';" 2>/dev/null \
  | tr -d ' \n')

echo "Storage path: $STORAGE_PATH"

LOGO_URL="/api/v1/media/view?path=$STORAGE_PATH"

echo ""
echo "=== Setting logo URL in brand visual ==="
curl -sf -X PUT "$API/api/v1/brand/visual" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"logo_url\": \"$LOGO_URL\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Logo set to: {d.get(\"logo_url\")}')
"

echo ""
echo "=== Adding as brand asset ==="
curl -sf -X POST "$API/api/v1/brand/assets" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"Brand Logo\", \"asset_type\": \"logo\", \"media_id\": \"$MEDIA_ID\", \"description\": \"Brand logo uploaded via script\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Asset added: {d.get(\"id\")}')
"

echo ""
echo "Done. Logo uploaded and set."
