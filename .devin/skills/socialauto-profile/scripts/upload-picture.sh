#!/usr/bin/env bash
# Upload a profile picture for a connected social account.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:-}"
FILE="${2:-}"
if [ -z "$ACCOUNT_ID" ] || [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: $(basename "$0") <account-id> <image-file>"
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

curl -sf -X POST "$API/api/v1/profile/$ACCOUNT_ID/picture" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$FILE" \
  | python3 -c 'import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))'
