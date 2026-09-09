#!/usr/bin/env bash
# Evaluate JavaScript in the browser and print the result.
# Usage: bridge-eval.sh <expression>
set -euo pipefail

EXPR="${1:?Usage: bridge-eval.sh <expression>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

curl -sf -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d "{\"expression\": \"$EXPR\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
result = d.get('result', '')
print(result)
"
