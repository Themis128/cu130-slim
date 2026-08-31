#!/usr/bin/env bash
# Check if a Meta access token is valid and show its expiry.
# Usage: bash check-meta-token.sh <access_token> [graph|threads]
set -euo pipefail

TOKEN="${1:?Usage: check-meta-token.sh <token> [graph|threads]}"
HOST="${2:-graph}"

if [ "$HOST" = "threads" ]; then
  API="https://graph.threads.net"
else
  API="https://graph.facebook.com"
fi

echo "Checking token at ${API}..."
echo ""

# Debug token
RESP=$(curl -s "${API}/debug_token?input_token=${TOKEN}&access_token=${TOKEN}")
echo "Debug response:"
echo "${RESP}" | python3 -m json.tool 2>/dev/null || echo "${RESP}"
