#!/usr/bin/env bash
# Check WhatsApp phone verification status and rate-limit state.
#
# Usage:
#   check-status.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

STATE_FILE="/tmp/whatsapp-verify-${ACCOUNT_ID}.state"

# Get admin token
TOKEN=$(cd /home/tbaltzakis/cu130-slim && set +u; source .env 2>/dev/null || true; set -u && \
  curl -s -X POST http://localhost:8083/api/v1/auth/login \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
    2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "❌ Could not authenticate with SocialAuto API" >&2
  exit 1
fi

# Check phone status
STATUS=$(curl -s "http://localhost:8083/api/v1/whatsapp/${ACCOUNT_ID}/phone/status" \
  -H "Authorization: Bearer $TOKEN" 2>/dev/null)

echo "$STATUS" | python3 -c "
import sys, json, os
from datetime import datetime, timezone

d = json.loads(sys.stdin.read())
status = d.get('code_verification_status', '?')
phone = d.get('display_phone_number', '?')
quality = d.get('quality_rating', '?')

state_file = '${STATE_FILE}'
rate_limited = False
cooldown_expires = ''
seconds_until = 0

if os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)
    cooldown_expires = state.get('cooldown_expires_at', '')
    if cooldown_expires:
        try:
            expiry = datetime.fromisoformat(cooldown_expires.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if now < expiry:
                rate_limited = True
                seconds_until = int((expiry - now).total_seconds())
        except:
            pass

print(f'Phone: {phone}')
print(f'Status: {status}')
print(f'Quality: {quality}')
print(f'Rate limited: {rate_limited}')
if rate_limited:
    print(f'Cooldown expires: {cooldown_expires}')
    mins = seconds_until // 60
    secs = seconds_until % 60
    print(f'Seconds until cooldown: {seconds_until} ({mins}m {secs}s)')
" 2>/dev/null

# Output JSON for programmatic use
if [[ "${2:-}" == "--json" ]]; then
  cat "$STATE_FILE" 2>/dev/null || echo "{}"
fi
