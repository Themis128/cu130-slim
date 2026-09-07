#!/usr/bin/env bash
# Login to Instagram via Facebook browser sidecar and extract sessionid.
#
# The Facebook sidecar has a logged-in FB session. We navigate to Instagram's
# "Log in with Facebook" flow, which uses the FB OIDC SSO. After landing on
# the Instagram feed, we extract the httpOnly sessionid cookie via
# Playwright's context.cookies() API (the /debug/all-cookies endpoint).
#
# Usage: login-via-facebook.sh [ig_username]
#   ig_username defaults to "cloudless.gr"
#
# Output: writes the sessionid to /tmp/ig-sessionid.txt and prints it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

FB_SIDECAR="${FB_SIDECAR_URL:-http://localhost:9226}"
IG_USERNAME="${1:-cloudless.gr}"

echo "=== Instagram Login via Facebook SSO ==="

# 1. Verify FB sidecar is logged in
FB_STATUS=$(curl -sf "$FB_SIDECAR/session" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('logged_in',''))" 2>/dev/null || echo "false")
if [[ "$FB_STATUS" != "True" ]]; then
  echo "ERROR: Facebook sidecar is not logged in."
  exit 1
fi
echo "✓ Facebook sidecar is logged in"

# 2. Navigate to Instagram login page
echo "→ Navigating to Instagram login..."
curl -sf -X POST "$FB_SIDECAR/debug/navigate" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.instagram.com/accounts/login/?force_classic_login=true"}' > /dev/null
sleep 3

# 3. Check if we see profile picker (FB SSO) or regular login
PAGE_TEXT=$(curl -sf "$FB_SIDECAR/debug/page-text" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null || echo "")

if echo "$PAGE_TEXT" | grep -q "$IG_USERNAME"; then
  # Profile picker — click the IG username
  echo "→ Found profile picker, clicking $IG_USERNAME..."
  curl -sf -X POST "$FB_SIDECAR/debug/eval" \
    -H "Content-Type: application/json" \
    -d "{\"script\":\"(async () => { const divs = document.querySelectorAll('div[role=button]'); for (const d of divs) { if (d.textContent.trim() === '$IG_USERNAME') { d.click(); return 'clicked'; } } return 'not found'; })()\"}" > /dev/null
  sleep 8
elif echo "$PAGE_TEXT" | grep -qi "Log in with Facebook"; then
  # Regular login page — click "Log in with Facebook"
  echo "→ Clicking 'Log in with Facebook'..."
  curl -sf -X POST "$FB_SIDECAR/debug/eval" \
    -H "Content-Type: application/json" \
    -d '{"script":"(async () => { const btns = document.querySelectorAll(\"button, div[role=button]\"); for (const b of btns) { if (b.textContent.includes(\"Facebook\")) { b.click(); return \"clicked\"; } } return \"not found\"; })()"}' > /dev/null
  sleep 5

  # Check if we landed on FB OIDC page with profile picker
  PAGE_TEXT2=$(curl -sf "$FB_SIDECAR/debug/page-text" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null || echo "")
  if echo "$PAGE_TEXT2" | grep -q "Continue"; then
    echo "→ On FB OIDC page, clicking Continue..."
    curl -sf -X POST "$FB_SIDECAR/debug/eval" \
      -H "Content-Type: application/json" \
      -d '{"script":"(async () => { const divs = document.querySelectorAll(\"div[role=button], button\"); for (const d of divs) { if (d.textContent.trim() === \"Continue\") { d.click(); await new Promise(r => setTimeout(r, 8000)); return document.URL; } } return \"not found\"; })()"}' > /dev/null
    sleep 5
  fi

  # Check if we now see the IG profile picker
  PAGE_TEXT3=$(curl -sf "$FB_SIDECAR/debug/page-text" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null || echo "")
  if echo "$PAGE_TEXT3" | grep -q "$IG_USERNAME"; then
    echo "→ Found profile picker, clicking $IG_USERNAME..."
    curl -sf -X POST "$FB_SIDECAR/debug/eval" \
      -H "Content-Type: application/json" \
      -d "{\"script\":\"(async () => { const divs = document.querySelectorAll('div[role=button]'); for (const d of divs) { if (d.textContent.trim() === '$IG_USERNAME') { d.click(); await new Promise(r => setTimeout(r, 8000)); return document.URL; } } return 'not found'; })()\"}" > /dev/null
    sleep 8
  fi
fi

# 4. Check if we're logged in to Instagram
CURRENT_URL=$(curl -sf -X POST "$FB_SIDECAR/debug/eval" \
  -H "Content-Type: application/json" \
  -d '{"script":"document.URL"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',''))" 2>/dev/null || echo "")
echo "→ Current URL: $CURRENT_URL"

if [[ "$CURRENT_URL" != *"instagram.com"* ]] || [[ "$CURRENT_URL" == *"login"* ]]; then
  echo "ERROR: Not logged in to Instagram. URL: $CURRENT_URL"
  exit 1
fi

# 5. Extract the sessionid cookie via Playwright context.cookies()
echo "→ Extracting Instagram cookies..."
COOKIES_JSON=$(curl -sf "$FB_SIDECAR/debug/all-cookies?domain=instagram.com" 2>/dev/null)

SESSIONID=$(echo "$COOKIES_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
cookies = d.get('cookies', {})
sid = cookies.get('sessionid', '')
if sid:
    print(sid)
else:
    print('')
" 2>/dev/null)

if [[ -z "$SESSIONID" ]]; then
  echo "ERROR: No sessionid cookie found. Available cookies:"
  echo "$COOKIES_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k in d.get('cookies', {}):
    print(f'  {k}')
" 2>/dev/null
  exit 1
fi

# 6. Save and output
echo "$SESSIONID" > /tmp/ig-sessionid.txt
echo "✓ SessionID extracted: ${SESSIONID:0:30}..."
echo "  Saved to /tmp/ig-sessionid.txt"

# 7. Login to the Instagram sidecar with this sessionid
echo ""
echo "→ Logging in to Instagram sidecar..."
SIDECAR_SID=$(curl -sf -X POST "http://localhost:8011/auth/login/by/sessionid" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "sessionid=$SESSIONID" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
sid = d.get('session_id') or d.get('sessionid') or ''
user = d.get('user', {})
print(f'{sid}\t{user.get(\"username\",\"\")}\t{user.get(\"pk\",\"\")}')
" 2>/dev/null || echo "")

if [[ -n "$SIDECAR_SID" ]]; then
  SID=$(echo "$SIDECAR_SID" | cut -f1)
  USERNAME=$(echo "$SIDECAR_SID" | cut -f2)
  PK=$(echo "$SIDECAR_SID" | cut -f3)
  echo "✓ Instagram sidecar login successful"
  echo "  Sidecar Session ID: ${SID:0:30}..."
  echo "  Username: $USERNAME"
  echo "  PK: $PK"
  echo "$SID" > /tmp/ig-sidecar-session.txt
  echo "  Saved sidecar session to /tmp/ig-sidecar-session.txt"
else
  echo "⚠ Sidecar login failed. SessionID may be expired."
fi
