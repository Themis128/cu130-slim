#!/usr/bin/env bash
# Request WhatsApp verification code via SMS or voice.
# Usage: request-code.sh <account_id> [SMS|VOICE] [language]
set -euo pipefail

ACCOUNT_ID="${1:-}"
METHOD="${2:-SMS}"
LANGUAGE="${3:-en_US}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [SMS|VOICE] [language]" >&2
  exit 1
fi

cd /home/tbaltzakis/cu130-slim
source .env 2>/dev/null || true

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "Error: Could not authenticate with SocialAuto" >&2
  exit 1
fi

echo "=== Requesting verification code for account $ACCOUNT_ID ===" >&2
echo "Method: $METHOD, Language: $LANGUAGE" >&2
curl -s -X POST "http://localhost:8083/api/v1/whatsapp/$ACCOUNT_ID/phone/request-code?code_method=$METHOD&language=$LANGUAGE" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
