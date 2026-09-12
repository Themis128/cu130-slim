#!/usr/bin/env bash
# Verify a platform session is active in the browser-novnc container
# and extract/persist cookies for cross-restart survival.
#
# Usage:
#   verify-session.sh <platform>
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <platform>" >&2
  echo "Platforms: twitter, threads, tiktok, instagram" >&2
  exit 1
fi

BRIDGE="http://localhost:9223"

# Use the check-session script from playwright-mcp-login skill
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="$SCRIPT_DIR/../playwright-mcp-login/scripts/check-session.sh"

if [[ -f "$CHECK_SCRIPT" ]]; then
  if bash "$CHECK_SCRIPT" "$PLATFORM" 2>/dev/null; then
    echo "✅ Session verified for $PLATFORM" >&2
  else
    echo "❌ Session NOT active for $PLATFORM" >&2
    exit 1
  fi
else
  # Inline check if the other script isn't available
  case "$PLATFORM" in
    twitter|x)
      URL="https://x.com/home"
      LOGIN_CHECK='!document.querySelector("a[href*=\"login\"]") && (document.querySelector("div[data-testid=\"SideNav_NewTweet_Button\"], a[href=\"/compose/post\"], nav[aria-label=\"Primary\"]") !== null)'
      ;;
    threads)
      URL="https://www.threads.com/"
      LOGIN_CHECK='!document.querySelector("a[href*=\"login\"]") && document.body.innerText.includes("Messages")'
      ;;
    tiktok)
      URL="https://www.tiktok.com/foryou"
      LOGIN_CHECK='document.querySelector("[data-e2e=\"profile-icon\"], a[href*=\"/profile\"]") !== null'
      ;;
    instagram)
      URL="https://www.instagram.com/"
      LOGIN_CHECK='!document.querySelector("a[href=\"/accounts/login/\"]")'
      ;;
    *)
      echo "Unknown platform: $PLATFORM" >&2
      exit 1
      ;;
  esac

  curl -s -X POST "$BRIDGE/session/navigate" \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"$URL\"}" >/dev/null 2>&1
  sleep 4

  RESULT=$(curl -s -X POST "$BRIDGE/session/evaluate" \
    -H "Content-Type: application/json" \
    -d "{\"expression\": \"(() => { try { return $LOGIN_CHECK; } catch(e) { return false; } })()\"}" \
    2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('result','false'))" 2>/dev/null || echo "false")

  if [[ "$RESULT" != "True" && "$RESULT" != "true" ]]; then
    echo "❌ Session NOT active for $PLATFORM" >&2
    exit 1
  fi
fi

# Extract and persist cookies
echo "Extracting cookies..." >&2
EXTRACT_RESULT=$(curl -s -X POST "$BRIDGE/session/extract" \
  -H "Content-Type: application/json" \
  -d "{\"platform\": \"$PLATFORM\"}" 2>/dev/null)

COOKIES_FOUND=$(echo "$EXTRACT_RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    cookies = d.get('cookies_found', d.get('cookies', []))
    if isinstance(cookies, dict):
        print(len(cookies))
    elif isinstance(cookies, list):
        print(len(cookies))
    else:
        print(d.get('cookies_found', '?'))
except:
    print('?')
" 2>/dev/null || echo "?")

echo "Cookies persisted: $COOKIES_FOUND" >&2
echo "✅ $PLATFORM session verified and cookies saved" >&2
exit 0
