#!/usr/bin/env bash
# Check if a platform session is active in the browser-novnc container.
#
# Usage:
#   check-session.sh <platform>
#
# Navigates to the platform and checks for login indicators.
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <platform>" >&2
  echo "Platforms: twitter, tiktok, threads, instagram" >&2
  exit 1
fi

case "$PLATFORM" in
  twitter|x)
    URL="https://x.com/home"
    LOGIN_CHECK='!document.querySelector(\"a[href*=\\\"login\\\"]\") && (document.querySelector(\"div[data-testid=\\\"SideNav_NewTweet_Button\\\"], a[href=\\\"/compose/post\\\"], nav[aria-label=\\\"Primary\\\"]\") !== null)'
    ;;
  tiktok)
    URL="https://www.tiktok.com/foryou"
    LOGIN_CHECK='document.querySelector(\"[data-e2e=\\\"profile-icon\\\"], a[href*=\\\"/profile\\\"]\") !== null'
    ;;
  threads)
    URL="https://www.threads.com/"
    LOGIN_CHECK='!document.querySelector(\"a[href*=\\\"login\\\"]\") && document.body.innerText.includes(\"Messages\")'
    ;;
  instagram)
    URL="https://www.instagram.com/"
    LOGIN_CHECK='!document.querySelector(\"a[href=\\\"/accounts/login/\\\"]\")'
    ;;
  *)
    echo "Unknown platform: $PLATFORM" >&2
    exit 1
    ;;
esac

BRIDGE="http://localhost:9223"

echo "=== Checking $PLATFORM session in browser-novnc ===" >&2

# 1. Navigate to the platform
echo "Navigating to $URL..." >&2
curl -s -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\"}" >/dev/null 2>&1

sleep 4

# 2. Check login state
echo "Checking login state..." >&2
RESULT=$(curl -s -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d "{\"expression\": \"(() => { try { return $LOGIN_CHECK; } catch(e) { return false; } })()\"}" \
  2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('result','false'))" 2>/dev/null || echo "false")

# 3. Get current URL
CURRENT_URL=$(curl -s -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"expression": "window.location.href"}' \
  2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('result','?'))" 2>/dev/null || echo "?")

echo "" >&2
echo "Current URL: $CURRENT_URL" >&2
echo "Logged in: $RESULT" >&2

if [[ "$RESULT" == "True" || "$RESULT" == "true" ]]; then
  echo "✅ Session active for $PLATFORM" >&2
  exit 0
else
  echo "❌ No active session for $PLATFORM" >&2
  exit 1
fi
