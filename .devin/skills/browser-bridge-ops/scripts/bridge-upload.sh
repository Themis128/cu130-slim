#!/usr/bin/env bash
# Upload a file to an input[type=file] element in the browser.
# Usage: bridge-upload.sh <file_path> <selector> [click_selector]
set -euo pipefail

FILE_PATH="${1:?Usage: bridge-upload.sh <file_path> <selector> [click_selector]}"
SELECTOR="${2:?Usage: bridge-upload.sh <file_path> <selector> [click_selector]}"
CLICK_SELECTOR="${3:-}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

if [ -n "$CLICK_SELECTOR" ]; then
  BODY="{\"selector\": \"$SELECTOR\", \"file_path\": \"$FILE_PATH\", \"click_selector\": \"$CLICK_SELECTOR\"}"
else
  BODY="{\"selector\": \"$SELECTOR\", \"file_path\": \"$FILE_PATH\"}"
fi

curl -sf -X POST "$BRIDGE/session/upload" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\", \"?\")}')
print(f'Uploaded: {d.get(\"uploaded\", False)}')
print(f'Method: {d.get(\"method\", \"?\")}')
"
