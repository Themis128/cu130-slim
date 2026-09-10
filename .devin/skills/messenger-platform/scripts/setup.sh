#!/usr/bin/env bash
# Set up Messenger on a Facebook Page (subscribe + default profile).
# Usage: setup.sh [account_id] [--greeting "Welcome text"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:-3f2f59c4-f190-44ad-aefe-4321af08ef89}"
shift 2>/dev/null || true

GREETING=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --greeting) GREETING="$2"; shift 2 ;;
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

BODY='{}'
if [ -n "$GREETING" ]; then
  BODY=$(python3 -c "import json; print(json.dumps({'greeting_text': '$GREETING'}))")
fi

echo "Setting up Messenger for account $ACCOUNT_ID..."
curl -sf -X POST "$API/api/v1/messenger/$ACCOUNT_ID/setup" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status:    {d.get(\"status\",\"?\")}')
print(f'Page:      {d.get(\"page_name\",\"?\")}')
print(f'URL:       {d.get(\"page_url\",\"?\")}')
sub = d.get('subscription', {})
print(f'Subscribed: {sub.get(\"success\", False)}')
prof = d.get('profile', {})
print(f'Profile:   {prof.get(\"result\", \"?\")}')
if prof.get('result') == 'rate_limited':
    print('  NOTE: Profile API rate limited. Subscription succeeded.')
    print('  Retry profile setup in 10 minutes.')
"
