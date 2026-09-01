#!/usr/bin/env bash
# Save a social secret in the SocialAuto secret store.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

KEY="${1:-}"
VALUE="${2:-}"
if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
  echo "Usage: $(basename "$0") <secret-key> <secret-value> [description]"
  exit 1
fi
DESCRIPTION="${3:-}"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

JSON_BODY="{\"value\":\"$(printf '%s' "$VALUE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()), end="")')\""
if [ -n "$DESCRIPTION" ]; then
  JSON_BODY+=",\"description\":\"$(printf '%s' "$DESCRIPTION" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()), end="")')\""
fi
JSON_BODY+="}"

curl -sf -X POST "$API/api/v1/secrets/$KEY" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$JSON_BODY" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["key"], d["sources"])'
