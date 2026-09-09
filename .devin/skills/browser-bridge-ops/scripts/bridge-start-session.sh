#!/usr/bin/env bash
# Start a browser session for a platform.
# Usage: bridge-start-session.sh <platform>
set -euo pipefail

PLATFORM="${1:?Usage: bridge-start-session.sh <platform>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

curl -sf -X POST "$BRIDGE/session/start" \
  -H "Content-Type: application/json" \
  -d "{\"platform\": \"$PLATFORM\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Platform: {d.get(\"platform\", \"?\")}')
print(f'Status: {d.get(\"status\", \"?\")}')
print(f'Message: {d.get(\"message\", \"?\")}')
print(f'noVNC: {d.get(\"novnc_url\", \"?\")}')
"
