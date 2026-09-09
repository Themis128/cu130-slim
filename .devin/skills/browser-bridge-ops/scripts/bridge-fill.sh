#!/usr/bin/env bash
# Fill a form field in the browser by CSS selector.
# Usage: bridge-fill.sh <selector> <value>
set -euo pipefail

SELECTOR="${1:?Usage: bridge-fill.sh <selector> <value>}"
VALUE="${2:?Usage: bridge-fill.sh <selector> <value>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

# Escape newlines in value for JSON
ESCAPED_VALUE=$(printf '%s' "$VALUE" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

curl -sf -X POST "$BRIDGE/session/fill" \
  -H "Content-Type: application/json" \
  -d "{\"selector\": \"$SELECTOR\", \"value\": $ESCAPED_VALUE}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\", \"?\")}')
print(f'Filled: {d.get(\"filled\", False)}')
"
