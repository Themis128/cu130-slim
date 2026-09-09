#!/usr/bin/env bash
# Find the best time to post for a specific account.
# Usage: best-time.sh --account-id <uuid>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --platform) shift 2 ;;  # Ignored for backward compat
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$ACCOUNT_ID" ]; then
  echo "Usage: best-time.sh --account-id <uuid>" >&2
  echo "  Find account IDs with: bash .devin/skills/socialauto-accounts/scripts/list-accounts.sh" >&2
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

BODY="{\"account_id\": \"$ACCOUNT_ID\"}"

curl -sf -X POST "$API/api/v1/ai/best-time-to-post" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -m json.tool
