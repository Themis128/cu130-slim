#!/usr/bin/env bash
# Test the WhatsApp webhook verification endpoint.
# Usage: bash check-webhook.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

API_BASE="${SOCIAL_API_URL:-http://localhost:8083}"
VERIFY_TOKEN="${WHATSAPP_VERIFY_TOKEN:-cloudless_whatsapp_verify}"

echo "=== WhatsApp Webhook Verification Test ==="
echo "Endpoint: $API_BASE/api/v1/whatsapp/webhook"
echo ""

# Test GET verification (Meta uses this to verify the webhook URL)
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -G "$API_BASE/api/v1/whatsapp/webhook" \
  --data-urlencode "hub.mode=subscribe" \
  --data-urlencode "hub.verify_token=$VERIFY_TOKEN" \
  --data-urlencode "hub.challenge=test_challenge_12345")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

echo "HTTP Status: $HTTP_CODE"
echo "Response Body: $BODY"
echo ""

if [ "$HTTP_CODE" = "200" ] && [ "$BODY" = "test_challenge_12345" ]; then
  echo "✓ Webhook verification PASSED — Meta can verify this endpoint"
else
  echo "✗ Webhook verification FAILED"
  echo "  Expected: 200 + 'test_challenge_12345'"
  echo "  Got: $HTTP_CODE + '$BODY'"
  exit 1
fi

# Test with wrong token (should return 403)
WRONG_RESPONSE=$(curl -s -w "\n%{http_code}" \
  -G "$API_BASE/api/v1/whatsapp/webhook" \
  --data-urlencode "hub.mode=subscribe" \
  --data-urlencode "hub.verify_token=wrong_token" \
  --data-urlencode "hub.challenge=should_not_see_this")

WRONG_CODE=$(echo "$WRONG_RESPONSE" | tail -1)

echo ""
echo "Wrong token test: HTTP $WRONG_CODE (expected 403)"
if [ "$WRONG_CODE" = "403" ]; then
  echo "✓ Correctly rejected invalid verify token"
else
  echo "✗ Should have returned 403 for wrong token"
fi
