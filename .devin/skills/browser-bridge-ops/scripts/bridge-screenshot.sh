#!/usr/bin/env bash
# Take a screenshot of the browser viewport.
# Saves to /tmp/browser-screenshot.png by default.
# Usage: bridge-screenshot.sh [output_path]
set -euo pipefail

OUTPUT="${1:-/tmp/browser-screenshot.png}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

# Use evaluate to trigger a screenshot via Playwright
curl -sf -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d "{\"expression\": \"window.location.href\"}" \
  > /dev/null 2>&1

# The bridge doesn't have a screenshot endpoint, so we use the noVNC
# web interface or suggest the user view it directly.
echo "Browser screenshot not available via API."
echo "View the browser at: http://localhost:6080/vnc.html"
echo "Current page:"
curl -sf "$BRIDGE/session/page-info" 2>&1 | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  URL: {d.get(\"url\", \"?\")}')
print(f'  Title: {d.get(\"title\", \"?\")}')
" 2>/dev/null || echo "  No active session"
