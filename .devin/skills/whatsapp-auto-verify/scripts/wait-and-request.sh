#!/usr/bin/env bash
# Wait for rate-limit cooldown to expire, then request a WhatsApp
# verification code. Handles Meta error 136024 automatically.
#
# Usage:
#   wait-and-request.sh <account_id> [SMS|VOICE] [language]
set -euo pipefail

ACCOUNT_ID="${1:-}"
CODE_METHOD="${2:-SMS}"
LANGUAGE="${3:-en_US}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [SMS|VOICE] [language]" >&2
  exit 1
fi

STATE_FILE="/tmp/whatsapp-verify-${ACCOUNT_ID}.state"
BRIDGE_API="http://localhost:8083/api/v1/whatsapp/${ACCOUNT_ID}/phone"

# Get admin token
cd /home/tbaltzakis/cu130-slim
set +u; source .env 2>/dev/null || true; set -u
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "❌ Could not authenticate with SocialAuto API" >&2
  exit 1
fi

# Check if we're in a cooldown
if [[ -f "$STATE_FILE" ]]; then
  COOLDOWN_EXPIRES=$(python3 -c "
import json, os
from datetime import datetime, timezone
try:
    with open('$STATE_FILE') as f:
        state = json.load(f)
    expiry = state.get('cooldown_expires_at', '')
    if expiry:
        e = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        if now < e:
            print(int((e - now).total_seconds()))
        else:
            print(0)
    else:
        print(0)
except:
    print(0)
" 2>/dev/null || echo "0")

  if [[ "$COOLDOWN_EXPIRES" -gt 0 ]]; then
    echo "⏳ Rate-limited. Waiting ${COOLDOWN_EXPIRES}s for cooldown to expire..." >&2
    # Sleep in 30-second increments to show progress
    REMAINING=$COOLDOWN_EXPIRES
    while [[ $REMAINING -gt 0 ]]; do
      SLEEP=$((REMAINING < 30 ? REMAINING : 30))
      echo "  ${REMAINING}s remaining..." >&2
      sleep $SLEEP
      REMAINING=$((REMAINING - SLEEP))
    done
    echo "✅ Cooldown expired" >&2
  fi
fi

# Request the code
echo "=== Requesting ${CODE_METHOD} code for ${ACCOUNT_ID} ===" >&2
RESULT=$(curl -s -X POST "${BRIDGE_API}/request-code" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"code_method\": \"${CODE_METHOD}\", \"language\": \"${LANGUAGE}\"}" 2>/dev/null)

# Check for rate-limit error
IS_RATE_LIMITED=$(echo "$RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    detail = d.get('detail', '')
    if isinstance(detail, str):
        if '136024' in detail or 'wait 1 hour' in detail.lower() or 'too many' in detail.lower():
            print('true')
        else:
            print('false')
    else:
        print('false')
except:
    print('false')
" 2>/dev/null || echo "false")

if [[ "$IS_RATE_LIMITED" == "true" ]]; then
  echo "❌ Rate-limited by Meta. Saving cooldown state..." >&2
  # Save state: cooldown expires in 1 hour
  python3 -c "
import json
from datetime import datetime, timezone, timedelta
state = {
    'account_id': '$ACCOUNT_ID',
    'rate_limited_at': datetime.now(timezone.utc).isoformat(),
    'cooldown_expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    'last_code_request_at': datetime.now(timezone.utc).isoformat(),
    'last_error': '136024'
}
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
print('State saved to $STATE_FILE')
" 2>/dev/null
  echo "⏳ Try again in 1 hour. Run this script again after the cooldown." >&2
  exit 1
fi

# Check for success
SUCCESS=$(echo "$RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    if 'detail' in d and isinstance(d['detail'], str) and 'error' in d['detail'].lower():
        print('false')
    elif d.get('status') == 'ok' or d.get('success') or 'code' not in str(d).lower():
        print('true')
    else:
        print('false')
except:
    print('false')
" 2>/dev/null || echo "false")

if [[ "$SUCCESS" == "true" ]]; then
  echo "✅ Verification code sent via ${CODE_METHOD}" >&2
  # Update state
  python3 -c "
import json, os
from datetime import datetime, timezone
state = {}
if os.path.exists('$STATE_FILE'):
    with open('$STATE_FILE') as f:
        state = json.load(f)
state['last_code_request_at'] = datetime.now(timezone.utc).isoformat()
state['rate_limited_at'] = ''
state['cooldown_expires_at'] = ''
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null
  exit 0
else
  echo "⚠️ Unexpected response:" >&2
  echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT" >&2
  exit 1
fi
