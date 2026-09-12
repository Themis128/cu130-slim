#!/usr/bin/env bash
# Poll the browser-novnc bridge until the platform session is active.
# Returns exit code 0 when logged in, 1 on timeout.
#
# Usage:
#   wait-for-login.sh <platform> [timeout_seconds]
set -euo pipefail

PLATFORM="${1:-}"
TIMEOUT="${2:-300}"

if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <platform> [timeout_seconds]" >&2
  echo "Platforms: twitter, threads, tiktok, instagram" >&2
  exit 1
fi

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

BRIDGE="http://localhost:9223"

echo "=== Waiting for $PLATFORM login (timeout: ${TIMEOUT}s) ===" >&2

# Navigate to the target URL to check login state
curl -s -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\"}" >/dev/null 2>&1

ELAPSED=0
INTERVAL=5
while [[ $ELAPSED -lt $TIMEOUT ]]; do
  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))

  RESULT=$(curl -s -X POST "$BRIDGE/session/evaluate" \
    -H "Content-Type: application/json" \
    -d "{\"expression\": \"(() => { try { return $LOGIN_CHECK; } catch(e) { return false; } })()\"}" \
    2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('result','false'))" 2>/dev/null || echo "false")

  if [[ "$RESULT" == "True" || "$RESULT" == "true" ]]; then
    echo "✅ $PLATFORM login detected after ${ELAPSED}s" >&2
    exit 0
  fi

  echo "  [$ELAPSED/${TIMEOUT}s] Not logged in yet..." >&2

  # Re-navigate every 30 seconds in case the page redirected
  if [[ $((ELAPSED % 30)) -eq 0 && $ELAPSED -gt 0 ]]; then
    curl -s -X POST "$BRIDGE/session/navigate" \
      -H "Content-Type: application/json" \
      -d "{\"url\": \"$URL\"}" >/dev/null 2>&1
  fi
done

echo "❌ Timeout waiting for $PLATFORM login after ${TIMEOUT}s" >&2
exit 1
