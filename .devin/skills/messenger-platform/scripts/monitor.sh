#!/bin/bash
# Lightweight Messenger bot monitor — checks internal sidecar stats only.
# Does NOT hit Meta APIs (no rate limit risk).
# Run: bash .devin/skills/messenger-platform/scripts/monitor.sh

set -e

API_URL="${SOCIAL_API_URL:-http://localhost:8083}"

echo "=== Messenger Bot Monitor ==="
echo "$(date)"
echo ""

# 1. Sidecar stats (internal, no Meta API)
echo "--- Sidecar Status ---"
curl -sf "$API_URL/api/v1/messenger/sidecar/status" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    stats = d.get('stats', {})
    health = d.get('health', {})
    print(f'Status:          {d.get(\"status\", \"unknown\")}')
    print(f'Health:          {health.get(\"status\", \"unknown\")}')
    print(f'Events received: {stats.get(\"events_received\", 0)}')
    print(f'Events processed:{stats.get(\"events_processed\", 0)}')
    print(f'Auto-replies:    {stats.get(\"auto_replies_sent\", 0)}')
    print(f'Errors:          {stats.get(\"errors\", 0)}')
    print(f'Dedup cache:     {stats.get(\"dedup_cache_size\", 0)}')
except:
    print('Sidecar unreachable')
" 2>&1

echo ""

# 2. Webhook endpoint (internal, no Meta API)
echo "--- Webhook Endpoint ---"
CHALLENGE=$(curl -sf "$API_URL/api/v1/messenger/webhook?hub.mode=subscribe&hub.verify_token=cloudless_messenger_verify&hub.challenge=monitor_test" 2>/dev/null || echo "FAILED")
if [ "$CHALLENGE" = "monitor_test" ]; then
    echo "Verification: OK"
else
    echo "Verification: FAILED (got: $CHALLENGE)"
fi

echo ""

# 3. API health (internal)
echo "--- API Health ---"
curl -sf "$API_URL/health" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'API: {d.get(\"status\", \"unknown\")}')
except:
    print('API unreachable')
" 2>&1

echo ""
echo "=== Monitor complete ==="
