#!/usr/bin/env bash
# Sync brand bio to a specific platform.
# Usage: sync-platform.sh <platform>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PLATFORM="${1:?Usage: sync-platform.sh <platform>}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Generate bio from brand profile
BIO=$("$ROOT/.devin/skills/brand-sync/scripts/generate-bio.sh")

echo "=== Generated bio ==="
echo "$BIO"
echo ""

# Get account ID for platform
ACCOUNT_ID=$(curl -sf "$API/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
for a in accounts:
    if a['platform'] == '$PLATFORM':
        print(a['id'])
        break
")

if [ -z "$ACCOUNT_ID" ]; then
  echo "No $PLATFORM account found."
  exit 1
fi

echo "=== Updating $PLATFORM (account: $ACCOUNT_ID) ==="

case "$PLATFORM" in
  threads)
    # Threads: use browser bridge
    echo "Updating Threads bio via browser bridge..."
    USERNAME=$(curl -sf "$API/api/v1/accounts" \
      -H "Authorization: Bearer $TOKEN" \
      | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
for a in accounts:
    if a['platform'] == 'threads':
        print(a.get('username', ''))
        break
")

    # Navigate to profile
    curl -sf -X POST "$BRIDGE/session/navigate" \
      -H "Content-Type: application/json" \
      -d "{\"url\": \"https://www.threads.com/@${USERNAME}\"}" > /dev/null
    sleep 3

    # Click Edit profile
    curl -sf -X POST "$BRIDGE/session/evaluate" \
      -H "Content-Type: application/json" \
      -d '{"expression": "(() => { const btns = document.querySelectorAll(\"div[role=button]\"); for (const btn of btns) { if (btn.textContent.trim() === \"Edit profile\") { btn.click(); return \"clicked\"; } } return \"not found\"; })()"}' > /dev/null
    sleep 2

    # Click Bio section
    curl -sf -X POST "$BRIDGE/session/evaluate" \
      -H "Content-Type: application/json" \
      -d '{"expression": "(() => { const dialog = document.querySelector(\"[role=dialog]\"); const all = dialog.querySelectorAll(\"div[role=button]\"); for (const el of all) { if (el.textContent.trim().startsWith(\"Bio\")) { el.click(); return \"clicked\"; } } return \"not found\"; })()"}' > /dev/null
    sleep 2

    # Fill bio
    ESCAPED_BIO=$(printf '%s' "$BIO" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
    curl -sf -X POST "$BRIDGE/session/fill" \
      -H "Content-Type: application/json" \
      -d "{\"selector\": \"textarea\", \"value\": $ESCAPED_BIO}" > /dev/null
    sleep 1

    # Click Done
    curl -sf -X POST "$BRIDGE/session/evaluate" \
      -H "Content-Type: application/json" \
      -d '{"expression": "(function() { const all = Array.from(document.querySelectorAll(\"div[role=button], button\")); const done = all.filter(b => b.innerText.trim() === \"Done\"); if (done.length > 0) { done[done.length - 1].click(); return \"clicked\"; } return \"no Done\"; })()"}' > /dev/null
    sleep 2

    # Click Done on main dialog
    curl -sf -X POST "$BRIDGE/session/evaluate" \
      -H "Content-Type: application/json" \
      -d '{"expression": "(function() { const all = Array.from(document.querySelectorAll(\"div[role=button], button\")); const done = all.filter(b => b.innerText.trim() === \"Done\"); if (done.length > 0) { done[done.length - 1].click(); return \"clicked\"; } return \"no Done\"; })()"}' > /dev/null
    sleep 3

    echo "Threads bio updated via browser bridge."
    ;;

  *)
    # Other platforms: use SocialAuto profile API
    echo "Updating $PLATFORM bio via SocialAuto profile API..."
    curl -sf -X PUT "$API/api/v1/profile/$ACCOUNT_ID" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"biography\": $(printf '%s' "$BIO" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")}" \
      | python3 -m json.tool 2>/dev/null || echo "Profile API update failed for $PLATFORM"
    ;;
esac

echo ""
echo "=== Done ==="
