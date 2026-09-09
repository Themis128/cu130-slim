#!/usr/bin/env bash
# Check if the Instagram browser session is logged in
# Usage: ./check-session.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "Session status:"
curl -s http://localhost:9223/session/status | python3 -m json.tool

echo ""
echo "Checking if logged in..."
curl -s -X POST http://localhost:9223/session/navigate \
    -H 'Content-Type: application/json' \
    -d '{"url":"https://www.instagram.com/"}' | python3 -m json.tool

sleep 4

RESULT=$(curl -s -X POST http://localhost:9223/session/evaluate \
    -H 'Content-Type: application/json' \
    -d '{"expression":"(document.body.innerText.includes(\"Log into\") || document.body.innerText.includes(\"Sign up\")) ? \"NOT_LOGGED_IN\" : \"LOGGED_IN\""}' | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('result',''))")

echo ""
if [ "$RESULT" = "LOGGED_IN" ]; then
    echo "✅ Logged in to Instagram"
    # Show which account
    curl -s -X POST http://localhost:9223/session/evaluate \
        -H 'Content-Type: application/json' \
        -d '{"expression":"document.body.innerText.substring(0, 100)"}' | \
        python3 -c "import sys,json; print('Feed preview:', json.load(sys.stdin).get('result','')[:100])"
else
    echo "❌ Not logged in — open http://localhost:6080/vnc.html and login"
fi
