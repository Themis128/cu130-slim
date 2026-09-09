#!/usr/bin/env bash
# Get current page info (URL and title).
set -euo pipefail

BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

curl -sf "$BRIDGE/session/page-info" 2>&1 | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'URL: {d.get(\"url\", \"?\")}')
print(f'Title: {d.get(\"title\", \"?\")}')
" 2>/dev/null || echo "No active session"
