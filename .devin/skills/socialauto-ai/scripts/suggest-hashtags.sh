#!/usr/bin/env bash
# Suggest hashtags for a topic.
# Usage: suggest-hashtags.sh "topic" [--platform linkedin]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TOPIC="${1:?Usage: suggest-hashtags.sh \"topic\" [--platform ...]}"
shift
PLATFORM="linkedin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
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
print(json.dumps({'topic': '''$TOPIC''', 'platform': '''$PLATFORM'''}))
")

curl -sf -X POST "$API/api/v1/ai/suggest-hashtags" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
tags = d.get('hashtags', d.get('data', {}).get('hashtags', []))
for t in tags:
    print(f'#{t}')
"
