#!/usr/bin/env bash
# Register WhatsApp phone number for Cloud API use.
# Usage: register-phone.sh <account_id> [6-digit-pin]
set -euo pipefail

ACCOUNT_ID="${1:-}"
PIN="${2:-}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [6-digit-pin]" >&2
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

echo "=== Registering phone for account $ACCOUNT_ID ===" >&2
if [[ -n "$PIN" ]]; then
  curl -s -X POST "http://localhost:8083/api/v1/whatsapp/$ACCOUNT_ID/phone/register" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"messaging_product\": \"whatsapp\", \"pin\": \"$PIN\"}" | python3 -m json.tool
else
  curl -s -X POST "http://localhost:8083/api/v1/whatsapp/$ACCOUNT_ID/phone/register" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"messaging_product": "whatsapp"}' | python3 -m json.tool
fi
