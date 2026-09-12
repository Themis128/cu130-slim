#!/usr/bin/env bash
# Start a browser session in the browser-novnc container and navigate to
# the platform's login page. Prints the noVNC URL for the user to open.
#
# Usage:
#   start-login.sh <platform>
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <platform>" >&2
  echo "Platforms: twitter, threads, tiktok, instagram" >&2
  exit 1
fi

case "$PLATFORM" in
  twitter|x)
    LOGIN_URL="https://x.com/i/flow/login"
    ;;
  threads)
    LOGIN_URL="https://www.threads.com/login/"
    ;;
  tiktok)
    LOGIN_URL="https://www.tiktok.com/login/phone-or-email/email"
    ;;
  instagram)
    LOGIN_URL="https://www.instagram.com/accounts/login/"
    ;;
  *)
    echo "Unknown platform: $PLATFORM" >&2
    exit 1
    ;;
esac

BRIDGE="http://localhost:9223"

echo "=== Starting $PLATFORM login session ===" >&2

# Stop any existing session
curl -s -X POST "$BRIDGE/session/stop" \
  -H "Content-Type: application/json" -d '{}' >/dev/null 2>&1 || true
sleep 1

# Start a new session for the platform
START_RESULT=$(curl -s -X POST "$BRIDGE/session/start" \
  -H "Content-Type: application/json" \
  -d "{\"platform\": \"$PLATFORM\"}" 2>/dev/null)

NOVNC_PATH=$(echo "$START_RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('novnc_url', '/novnc/vnc.html?autoconnect=1&resize=scale'))
except:
    print('/novnc/vnc.html?autoconnect=1&resize=scale')
" 2>/dev/null)

sleep 2

# Navigate to the login page
echo "Navigating to $LOGIN_URL..." >&2
curl -s -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$LOGIN_URL\"}" >/dev/null 2>&1

sleep 3

echo "" >&2
echo "========================================" >&2
echo "  noVNC URL: http://localhost:6080${NOVNC_PATH}" >&2
echo "========================================" >&2
echo "" >&2
echo "Open the noVNC URL in your browser and complete the login for $PLATFORM." >&2
echo "Then run: bash scripts/wait-for-login.sh $PLATFORM" >&2
echo "" >&2
echo "http://localhost:6080${NOVNC_PATH}"
