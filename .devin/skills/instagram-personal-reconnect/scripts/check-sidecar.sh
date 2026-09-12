#!/usr/bin/env bash
# Check if the instagrapi sidecar is healthy and running.
set -euo pipefail

SIDECAR="http://localhost:8011"

echo "=== Checking instagrapi sidecar ===" >&2

RESPONSE=$(curl -s -m 5 "${SIDECAR}/health" 2>/dev/null || echo "")

if [[ -z "$RESPONSE" ]]; then
  echo "❌ Sidecar not responding at ${SIDECAR}" >&2
  exit 1
fi

STATUS=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('status', d.get('healthy', 'unknown')))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

if [[ "$STATUS" == "ok" || "$STATUS" == "healthy" || "$STATUS" == "true" ]]; then
  echo "✅ Sidecar healthy" >&2
  exit 0
else
  echo "⚠️ Sidecar responded but status: $STATUS" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
