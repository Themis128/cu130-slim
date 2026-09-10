#!/usr/bin/env bash
# Test webhook verification and event processing.
# Usage: webhook-test.sh [--verify | --message "text" | --postback "payload"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
PAGE_ID="116436681562585"
VERIFY_TOKEN=$(grep -E '^MESSENGER_VERIFY_TOKEN=' .env 2>/dev/null | cut -d= -f2- || echo "cloudless_messenger_verify")

# Test GET verification
test_verify() {
  echo "=== Webhook GET verification ==="
  echo "--- Correct token ---"
  RESP=$(curl -s -w "\n%{http_code}" \
    "$API/api/v1/messenger/webhook?hub.mode=subscribe&hub.verify_token=$VERIFY_TOKEN&hub.challenge=test123")
  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE, Body: $BODY"
  if [ "$CODE" = "200" ] && [ "$BODY" = '"test123"' ]; then
    echo "  PASS"
  else
    echo "  FAIL"
  fi

  echo "--- Wrong token ---"
  RESP=$(curl -s -w "\n%{http_code}" \
    "$API/api/v1/messenger/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=test123")
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE"
  if [ "$CODE" = "403" ]; then echo "  PASS"; else echo "  FAIL"; fi

  echo "--- Missing params ---"
  RESP=$(curl -s -w "\n%{http_code}" "$API/api/v1/messenger/webhook")
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE"
  if [ "$CODE" = "403" ]; then echo "  PASS"; else echo "  FAIL"; fi
}

# Test POST with simulated message
test_message() {
  local text="${1:-Hello from test}"
  echo "=== Webhook POST (text message) ==="
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" \
    -d "{\"object\":\"page\",\"entry\":[{\"id\":\"$PAGE_ID\",\"messaging\":[{\"sender\":{\"id\":\"test_psid_123\"},\"recipient\":{\"id\":\"$PAGE_ID\"},\"message\":{\"mid\":\"m_test\",\"text\":\"$text\"}}],\"time\":1700000000000}]}" \
    "$API/api/v1/messenger/webhook")
  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE, Body: $BODY"
  if [ "$CODE" = "200" ]; then echo "  PASS"; else echo "  FAIL"; fi
}

# Test POST with simulated postback
test_postback() {
  local payload="${1:-GET_STARTED}"
  echo "=== Webhook POST (postback: $payload) ==="
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" \
    -d "{\"object\":\"page\",\"entry\":[{\"id\":\"$PAGE_ID\",\"messaging\":[{\"sender\":{\"id\":\"test_psid_456\"},\"recipient\":{\"id\":\"$PAGE_ID\"},\"postback\":{\"payload\":\"$payload\"}}],\"time\":1700000000001}]}" \
    "$API/api/v1/messenger/webhook")
  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE, Body: $BODY"
  if [ "$CODE" = "200" ]; then echo "  PASS"; else echo "  FAIL"; fi
}

# Test POST with empty body
test_empty() {
  echo "=== Webhook POST (empty body) ==="
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" \
    -d '{}' "$API/api/v1/messenger/webhook")
  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n1)
  echo "  HTTP $CODE, Body: $BODY"
  if [ "$CODE" = "200" ]; then echo "  PASS"; else echo "  FAIL"; fi
}

# Run all tests if no args
if [ $# -eq 0 ]; then
  test_verify
  echo ""
  test_empty
  echo ""
  test_message "Hello from test"
  echo ""
  test_postback "GET_STARTED"
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify) test_verify; shift ;;
    --message) test_message "$2"; shift 2 ;;
    --postback) test_postback "$2"; shift 2 ;;
    --empty) test_empty; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done
