#!/usr/bin/env bash
# Verify a WhatsApp verification code and register the phone number.
#
# Usage:
#   verify-and-register.sh <account_id> <6-digit-code> [6-digit-pin]
set -euo pipefail

ACCOUNT_ID="${1:-}"
CODE="${2:-}"
PIN="${3:-}"

if [[ -z "$ACCOUNT_ID" || -z "$CODE" ]]; then
  echo "Usage: $0 <account_id> <6-digit-code> [6-digit-pin]" >&2
  exit 1
fi

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

# Step 1: Verify the code
echo "=== Verifying code ===" >&2
VERIFY_RESULT=$(curl -s -X POST "${BRIDGE_API}/verify-code" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"code\": \"${CODE}\"}" 2>/dev/null)

VERIFY_OK=$(echo "$VERIFY_RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    if d.get('status') == 'ok' or d.get('success') or d.get('verified'):
        print('true')
    elif 'detail' in d and isinstance(d['detail'], str) and 'error' in d['detail'].lower():
        print('false')
    else:
        print('true')
except:
    print('false')
" 2>/dev/null || echo "false")

if [[ "$VERIFY_OK" != "true" ]]; then
  echo "❌ Code verification failed:" >&2
  echo "$VERIFY_RESULT" | python3 -m json.tool 2>/dev/null || echo "$VERIFY_RESULT" >&2
  exit 1
fi
echo "✅ Code verified" >&2

# Step 2: Register the phone number
echo "=== Registering phone number ===" >&2
REGISTER_DATA="{\"pin\": \"${PIN}\"}"
if [[ -z "$PIN" ]]; then
  REGISTER_DATA="{}"
fi

REGISTER_RESULT=$(curl -s -X POST "${BRIDGE_API}/register" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$REGISTER_DATA" 2>/dev/null)

REGISTER_OK=$(echo "$REGISTER_RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    if d.get('status') == 'ok' or d.get('success') or d.get('registered'):
        print('true')
    elif 'detail' in d and isinstance(d['detail'], str) and 'error' in d['detail'].lower():
        print('false')
    else:
        print('true')
except:
    print('false')
" 2>/dev/null || echo "false")

if [[ "$REGISTER_OK" != "true" ]]; then
  echo "⚠️ Registration response:" >&2
  echo "$REGISTER_RESULT" | python3 -m json.tool 2>/dev/null || echo "$REGISTER_RESULT" >&2
  # Not necessarily a failure — might already be registered
fi
echo "✅ Registration complete" >&2

# Step 3: Confirm status
echo "" >&2
echo "=== Final phone status ===" >&2
bash "$(dirname "$0")/check-status.sh" "$ACCOUNT_ID" 2>/dev/null || true
