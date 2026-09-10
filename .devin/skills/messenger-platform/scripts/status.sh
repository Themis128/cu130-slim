#!/usr/bin/env bash
# Check Messenger setup status for a Facebook Page.
# Usage: status.sh [account_id]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ACCOUNT_ID="${1:-3f2f59c4-f190-44ad-aefe-4321af08ef89}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "=== Messenger Status for account $ACCOUNT_ID ==="
echo ""

# Get profile
PROFILE=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/messenger/$ACCOUNT_ID/profile" 2>/dev/null || echo '{}')

python3 -c "
import json
p = json.loads('''$PROFILE''')
print(f'Page ID:       {p.get(\"page_id\", \"?\")}')
print(f'Page Name:     {p.get(\"page_name\", \"?\")}')
print(f'Subscribed:    {p.get(\"subscribed\", False)}')
print(f'Get Started:   {\"yes\" if p.get(\"get_started\") else \"no\"}')
print(f'Persistent Menu: {\"yes\" if p.get(\"persistent_menu\") else \"no\"}')
print(f'Whitelisted:   {p.get(\"whitelisted_domains\", [])}')
print(f'Ice Breakers:  {\"yes\" if p.get(\"ice_breakers\") else \"no\"}')
print(f'Greeting:      {\"yes\" if p.get(\"greeting\") else \"no (deprecated by Meta)\"}')
"

echo ""

# Get auto-reply config
AUTOREPLY=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/messenger/$ACCOUNT_ID/auto-reply" 2>/dev/null || echo '{}')

python3 -c "
import json
a = json.loads('''$AUTOREPLY''')
print(f'Auto-Reply:    {\"ENABLED\" if a.get(\"enabled\") else \"disabled\"}')
print(f'Model:         {a.get(\"model\", \"?\")}')
print(f'Max Tokens:    {a.get(\"max_tokens\", \"?\")}')
print(f'Fallback:      {a.get(\"fallback_text\", \"?\")[:60]}...')
"

echo ""

# Get conversations count
CONVOS=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/messenger/$ACCOUNT_ID/conversations?limit=100" 2>/dev/null || echo '[]')

python3 -c "
import json
c = json.loads('''$CONVOS''')
print(f'Conversations: {len(c)}')
"
