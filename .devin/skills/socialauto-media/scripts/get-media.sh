#!/usr/bin/env bash
# Get media asset details.
# Usage: get-media.sh <media-id>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ID="${1:?Usage: get-media.sh <media-id>}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sf "$API/api/v1/media/$ID" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'ID: {d.get(\"id\",\"?\")}')
print(f'Filename: {d.get(\"filename\",\"?\")}')
print(f'MIME: {d.get(\"mime_type\",\"?\")}')
print(f'Size: {d.get(\"size_bytes\",0)} bytes')
print(f'Dimensions: {d.get(\"width\",\"?\")}x{d.get(\"height\",\"?\")}')
print(f'Alt text: {d.get(\"alt_text\",\"-\")}')
print(f'AI caption: {d.get(\"ai_caption\",\"-\")}')
print(f'Tags: {d.get(\"tags\",[])}')
print(f'Source: {d.get(\"source\",\"-\")}')
print(f'Storage path: {d.get(\"storage_path\",\"-\")}')
print(f'View URL: {d.get(\"storage_path\",\"\") and \"/api/v1/media/view?path=\" + d[\"storage_path\"] or \"-\"}')
"
