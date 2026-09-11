#!/usr/bin/env bash
# Send a test WhatsApp message via the SocialAuto API.
# Usage: bash send-test-message.sh <account_id> <recipient_phone> <message>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

API_BASE="${SOCIAL_API_URL:-http://localhost:8083}"

if [ $# -lt 3 ]; then
  echo "Usage: $0 <account_id> <recipient_phone> <message>"
  echo "  account_id    UUID of the WhatsApp account in SocialAuto"
  echo "  recipient_phone  E.164 phone (e.g. +3069XXXXXXXX or 3069XXXXXXXX)"
  echo "  message       Text to send"
  echo ""
  echo "Example: $0 abc-123 +30691234567 'Hello from Cloudless!'"
  exit 1
fi

ACCOUNT_ID="$1"
RECIPIENT="$2"
MESSAGE="$3"

# Get auth token
TOKEN_FILE="$REPO_ROOT/.whatsapp_test_token"
if [ ! -f "$TOKEN_FILE" ]; then
  echo "No test token found at $TOKEN_FILE"
  echo "Create it with: echo 'your_jwt_token' > $TOKEN_FILE"
  exit 1
fi
TOKEN=$(cat "$TOKEN_FILE" | tr -d '[:space:]')

echo "=== Sending WhatsApp Test Message ==="
echo "Account: $ACCOUNT_ID"
echo "To: $RECIPIENT"
echo "Message: $MESSAGE"
echo ""

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "$API_BASE/api/v1/whatsapp/$ACCOUNT_ID/send" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "to": "$RECIPIENT",
  "text": "$MESSAGE",
  "messaging_type": "RESPONSE"
}
EOF
)")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

echo "HTTP Status: $HTTP_CODE"
echo "Response: $BODY"

if [ "$HTTP_CODE" = "200" ]; then
  echo ""
  echo "✓ Message sent successfully"
else
  echo ""
  echo "✗ Failed to send message"
  exit 1
fi
