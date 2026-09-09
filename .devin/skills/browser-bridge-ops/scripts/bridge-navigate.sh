#!/usr/bin/env bash
# Navigate the browser to a URL.
# Usage: bridge-navigate.sh <url>
set -euo pipefail

URL="${1:?Usage: bridge-navigate.sh <url>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

curl -sf -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\"}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'URL: {d.get(\"url\", \"?\")}')
print(f'Title: {d.get(\"title\", \"?\")}')
"
