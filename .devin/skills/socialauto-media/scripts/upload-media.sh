#!/usr/bin/env bash
# Upload a file to the media library.
# Usage: upload-media.sh <file-path> [--alt "description"] [--tags "tag1,tag2"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

FILE="${1:?Usage: upload-media.sh <file-path> [--alt ...] [--tags ...]}"
shift
ALT=""
TAGS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --alt) ALT="$2"; shift 2 ;;
    --tags) TAGS="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "Uploading $FILE..."
CURL_ARGS=(-s -X POST "$API/api/v1/media/upload" -H "Authorization: Bearer $TOKEN" -F "file=@$FILE")
[[ -n "$ALT" ]] && CURL_ARGS+=(-F "alt_text=$ALT")
[[ -n "$TAGS" ]] && CURL_ARGS+=(-F "tags=$TAGS")

curl "${CURL_ARGS[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Media ID: {d.get(\"id\",\"?\")}')
print(f'Filename: {d.get(\"filename\",\"?\")}')
print(f'MIME: {d.get(\"mime_type\",\"?\")}')
print(f'Size: {d.get(\"size_bytes\",0)} bytes')
"
