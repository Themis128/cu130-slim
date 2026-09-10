#!/usr/bin/env bash
# Get or set AI auto-reply configuration for Messenger.
# Usage: auto-reply.sh <account_id> [--enable | --disable | --prompt "text" | --model "name" | --fallback "text"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:?Usage: auto-reply.sh <account_id> [--enable | --disable | --prompt ... | --model ... | --fallback ...]}"
shift

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# If no flags, just get current config
if [ $# -eq 0 ]; then
  echo "Current auto-reply config:"
  curl -sf -H "Authorization: Bearer $TOKEN" \
    "$API/api/v1/messenger/$ACCOUNT_ID/auto-reply" \
    | python3 -m json.tool
  exit 0
fi

# Get current config first, then apply changes
CURRENT=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/messenger/$ACCOUNT_ID/auto-reply")

BODY=$(python3 -c "
import json, sys
config = json.loads('''$CURRENT''')
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == '--enable':
        config['enabled'] = True
        i += 1
    elif args[i] == '--disable':
        config['enabled'] = False
        i += 1
    elif args[i] == '--prompt':
        config['system_prompt'] = args[i+1]
        i += 2
    elif args[i] == '--model':
        config['model'] = args[i+1]
        i += 2
    elif args[i] == '--fallback':
        config['fallback_text'] = args[i+1]
        i += 2
    elif args[i] == '--max-tokens':
        config['max_tokens'] = int(args[i+1])
        i += 2
    else:
        i += 1
print(json.dumps(config))
" "$@")

echo "Updating auto-reply config..."
curl -sf -X PUT "$API/api/v1/messenger/$ACCOUNT_ID/auto-reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -m json.tool
