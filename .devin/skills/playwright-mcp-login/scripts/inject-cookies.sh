#!/usr/bin/env bash
# Inject cookies into the browser-novnc container (port 9223).
#
# Usage:
#   inject-cookies.sh <platform> <cookies-json-file>
#
# Reads a JSON array of cookies and sets them in the browser-novnc browser
# by navigating to the platform domain and using document.cookie for each
# non-HTTP-only cookie. HTTP-only cookies require the Playwright context
# directly (not available via the bridge API — use the /session/cookies
# endpoint if the bridge supports it).
set -euo pipefail

PLATFORM="${1:-}"
COOKIES_FILE="${2:-}"

if [[ -z "$PLATFORM" || -z "$COOKIES_FILE" ]]; then
  echo "Usage: $0 <platform> <cookies-json-file>" >&2
  exit 1
fi

case "$PLATFORM" in
  twitter|x)
    URL="https://x.com"
    DOMAIN=".x.com"
    ;;
  tiktok)
    URL="https://www.tiktok.com"
    DOMAIN=".tiktok.com"
    ;;
  threads)
    URL="https://www.threads.com"
    DOMAIN=".threads.com"
    ;;
  instagram)
    URL="https://www.instagram.com"
    DOMAIN=".instagram.com"
    ;;
  *)
    echo "Unknown platform: $PLATFORM" >&2
    exit 1
    ;;
esac

BRIDGE="http://localhost:9223"

echo "=== Injecting cookies for $PLATFORM ===" >&2
echo "URL: $URL" >&2
echo "Cookies file: $COOKIES_FILE" >&2

# 1. Navigate the browser-novnc browser to the platform domain
echo "Navigating browser-novnc to $URL..." >&2
curl -s -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\"}" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status','?'))" 2>/dev/null || echo "navigate failed" >&2

sleep 3

# 2. Set each cookie via document.cookie
# Parse the JSON file and set each cookie
python3 -c "
import json, sys, urllib.request

with open('$COOKIES_FILE') as f:
    cookies = json.load(f)

bridge = '$BRIDGE'

for cookie in cookies:
    name = cookie.get('name', '')
    value = cookie.get('value', '')
    domain = cookie.get('domain', '$DOMAIN')
    path = cookie.get('path', '/')
    secure = cookie.get('secure', True)
    http_only = cookie.get('httpOnly', False)

    if http_only:
        print(f'  SKIP (httpOnly): {name}', file=sys.stderr)
        continue

    # Build document.cookie string
    cookie_str = f'{name}={value}; domain={domain}; path={path}'
    if secure:
        cookie_str += '; secure'
    cookie_str += '; max-age=86400'

    # Escape for JS
    escaped = cookie_str.replace("'", "\\'")

    expr = f\"document.cookie = '{escaped}'\"

    try:
        req = urllib.request.Request(
            f'{bridge}/session/evaluate',
            data=json.dumps({'expression': expr}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f'  SET: {name}={value[:20]}...', file=sys.stderr)
    except Exception as e:
        print(f'  FAIL: {name}: {e}', file=sys.stderr)

print('Cookie injection complete', file=sys.stderr)
"

# 3. Reload the page to apply cookies
echo "Reloading page..." >&2
curl -s -X POST "$BRIDGE/session/navigate" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\"}" >/dev/null 2>&1

sleep 3
echo "Done. Check session with check-session.sh $PLATFORM" >&2
