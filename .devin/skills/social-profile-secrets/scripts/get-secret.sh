#!/usr/bin/env bash
# Get a raw secret value from the SocialAuto secret store.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

KEY="${1:-}"
if [ -z "$KEY" ]; then
  echo "Usage: $(basename "$0") <secret-key>"
  exit 1
fi

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sf "$API/api/v1/secrets/$KEY" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("value",""))'
