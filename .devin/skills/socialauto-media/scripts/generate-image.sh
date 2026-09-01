#!/usr/bin/env bash
# Generate an AI image and save to media library.
# Usage: generate-image.sh "prompt text" [--model "@cf/black-forest-labs/flux-1-schnell"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PROMPT="${1:?Usage: generate-image.sh \"prompt\" [--model ...]}"
shift
MODEL="@cf/black-forest-labs/flux-1-schnell"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
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

BODY=$(python3 -c "
import json
print(json.dumps({'prompt': '''$PROMPT''', 'options': {'provider': 'cloudflare', 'model': '''$MODEL''', 'steps': 4}}))
")

echo "Generating image: $PROMPT"
curl -sf -X POST "$API/api/v1/ai/generate-image-pipeline" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Media ID: {d.get(\"media_id\", d.get(\"id\",\"?\"))}')
print(f'URL: {d.get(\"url\",\"-\")}')
print(f'Status: {d.get(\"status\",\"?\")}')
"
