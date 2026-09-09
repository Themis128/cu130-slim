#!/usr/bin/env bash
# Click an element in the browser by CSS selector.
# Usage: bridge-click.sh <selector>
set -euo pipefail

SELECTOR="${1:?Usage: bridge-click.sh <selector>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

curl -sf -X POST "$BRIDGE/session/click" \
  -H "Content-Type: application/json" \
  -d "{\"selector\": \"$SELECTOR\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\", \"?\")}')
print(f'Clicked: {d.get(\"clicked\", False)}')
"
