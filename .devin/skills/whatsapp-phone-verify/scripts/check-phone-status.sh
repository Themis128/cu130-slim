#!/usr/bin/env bash
# Check WhatsApp phone registration status.
# Usage: check-phone-status.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

# Load .env for admin credentials
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

echo "=== WhatsApp Phone Status for account $ACCOUNT_ID ===" >&2
curl -s "http://localhost:8083/api/v1/whatsapp/$ACCOUNT_ID/phone/status" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
