#!/usr/bin/env bash
# Start Instagram browser session and wait for VNC login
# Usage: ./start-session.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "Stopping any existing browser session..."
curl -s -X POST http://localhost:9223/session/stop 2>/dev/null | python3 -m json.tool 2>/dev/null || true
sleep 2

echo "Starting Instagram browser session..."
curl -s -X POST http://localhost:9223/session/start \
    -H 'Content-Type: application/json' \
    -d '{"platform":"instagram"}' | python3 -m json.tool

echo ""
echo "========================================"
echo "  Open this URL to log in via VNC:"
echo "  http://localhost:6080/vnc.html"
echo "========================================"
echo ""
echo "Click 'Log in with Facebook' on the Instagram login page."
echo "Select the correct account if multiple appear."
echo "Once you see your Instagram feed, run:"
echo ""
echo "  ./check-session.sh"
echo ""
echo "to verify the login."
