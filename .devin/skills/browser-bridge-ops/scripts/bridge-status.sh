#!/usr/bin/env bash
# Check browser bridge health and current session status.
set -euo pipefail

BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

echo "=== Bridge health ==="
curl -sf "$BRIDGE/health" 2>&1 | python3 -m json.tool 2>/dev/null || echo "Bridge not responding"

echo ""
echo "=== Session status ==="
curl -sf "$BRIDGE/session/status" 2>&1 | python3 -m json.tool 2>/dev/null || echo "No active session"

echo ""
echo "=== Page info ==="
curl -sf "$BRIDGE/session/page-info" 2>&1 | python3 -m json.tool 2>/dev/null || echo "No page info"
