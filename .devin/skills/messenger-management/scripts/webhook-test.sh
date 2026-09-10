#!/usr/bin/env bash
# Test Messenger webhook verification + event dispatch
# Usage: ./webhook-test.sh [page_id]
set -euo pipefail
cd "$(dirname "$0")/../../.."

source .env 2>/dev/null || true

PAGE_ID="${1:-116436681562585}"
VERIFY_TOKEN="${MESSENGER_VERIFY_TOKEN:-cloudless_messenger_verify}"

echo "=== 1. Webhook Verification (GET) ==="
curl -s "http://localhost:8083/api/v1/messenger/webhook?hub.mode=subscribe&hub.verify_token=${VERIFY_TOKEN}&hub.challenge=test123"
echo ""

echo ""
echo "=== 2. Webhook Event Dispatch (POST) ==="
MID="m_test_$(date +%s)"
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"object\":\"page\",\"entry\":[{\"id\":\"${PAGE_ID}\",\"messaging\":[{\"sender\":{\"id\":\"test_psid\"},\"recipient\":{\"id\":\"${PAGE_ID}\"},\"message\":{\"mid\":\"${MID}\",\"text\":\"Test from webhook-test.sh\"}}],\"time\":$(date +%s)000}]}" \
  http://localhost:8083/api/v1/messenger/webhook | python3 -m json.tool

sleep 2
echo ""
echo "=== 3. Sidecar Stats ==="
curl -s http://localhost:9230/stats | python3 -m json.tool
