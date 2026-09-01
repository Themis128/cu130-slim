#!/usr/bin/env bash
# Generate post copy for a specific platform.
# Usage: generate-content.sh "topic" --platform linkedin --tone professional
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TOPIC="${1:?Usage: generate-content.sh \"topic\" --platform ... --tone ...}"
shift
PLATFORM="linkedin"
TONE="professional"
LENGTH="medium"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --tone) TONE="$2"; shift 2 ;;
    --length) LENGTH="$2"; shift 2 ;;
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
print(json.dumps({'prompt': '''$TOPIC''', 'platform': '''$PLATFORM''', 'tone': '''$TONE''', 'length': '''$LENGTH'''}))
")

curl -sf -X POST "$API/api/v1/ai/generate-content" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
content = d.get('data', {}).get('content', d.get('content', ''))
print(content)
"
