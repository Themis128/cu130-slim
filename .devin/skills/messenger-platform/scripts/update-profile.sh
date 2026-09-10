#!/usr/bin/env bash
# Update Messenger Profile properties.
# Usage: update-profile.sh [account_id] --greeting "text" | --menu 'json' | --domains '["url"]' | --ice-breakers 'json'
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:-3f2f59c4-f190-44ad-aefe-4321af08ef89}"
shift 2>/dev/null || true

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

BODY=$(python3 -c "
import json, sys
d = {}
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == '--greeting':
        d['greeting'] = [{'locale': 'default', 'text': args[i+1]}]
        d['get_started'] = {'payload': 'GET_STARTED'}  # required by Graph API
        i += 2
    elif args[i] == '--menu':
        d['persistent_menu'] = json.loads(args[i+1])
        i += 2
    elif args[i] == '--domains':
        d['whitelisted_domains'] = json.loads(args[i+1])
        i += 2
    elif args[i] == '--ice-breakers':
        d['ice_breakers'] = json.loads(args[i+1])
        i += 2
    else:
        i += 1
if not d:
    print(json.dumps({}))
    sys.exit(0)
print(json.dumps(d))
" "$@")

if [ "$BODY" = "{}" ]; then
  echo "Usage: update-profile.sh [account_id] --greeting 'text' | --menu 'json' | --domains 'json' | --ice-breakers 'json'"
  exit 1
fi

echo "Updating Messenger Profile..."
curl -sf -X PUT "$API/api/v1/messenger/$ACCOUNT_ID/profile" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -m json.tool
