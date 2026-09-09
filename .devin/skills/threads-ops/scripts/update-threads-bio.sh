#!/usr/bin/env bash
# Update Threads profile bio via browser bridge.
# Usage: update-threads-bio.sh <username> <bio>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

USERNAME="${1:?Usage: update-threads-bio.sh <username> <bio>}"
BIO="${2:?Usage: update-threads-bio.sh <username> <bio>}"
BRIDGE="${BROWSER_BRIDGE_URL:-http://localhost:9223}"

echo "=== Updating Threads bio for @$USERNAME ==="

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
  -d '{"expression": "(() => { const dialog = document.querySelector(\"[role=dialog]\"); if (!dialog) return \"no dialog\"; const all = dialog.querySelectorAll(\"div[role=button]\"); for (const el of all) { if (el.textContent.trim().startsWith(\"Bio\")) { el.click(); return \"clicked\"; } } return \"not found\"; })()"}' > /dev/null
sleep 2

# Fill bio textarea
ESCAPED_BIO=$(printf '%s' "$BIO" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
curl -sf -X POST "$BRIDGE/session/fill" \
  -H "Content-Type: application/json" \
  -d "{\"selector\": \"textarea\", \"value\": $ESCAPED_BIO}" > /dev/null
sleep 1

# Click Done to save bio
curl -sf -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"expression": "(function() { const all = Array.from(document.querySelectorAll(\"div[role=button], button\")); const done = all.filter(b => b.innerText.trim() === \"Done\"); if (done.length > 0) { done[done.length - 1].click(); return \"clicked\"; } return \"no Done\"; })()"}' > /dev/null
sleep 2

# Click Done on main dialog
curl -sf -X POST "$BRIDGE/session/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"expression": "(function() { const all = Array.from(document.querySelectorAll(\"div[role=button], button\")); const done = all.filter(b => b.innerText.trim() === \"Done\"); if (done.length > 0) { done[done.length - 1].click(); return \"clicked\"; } return \"no Done\"; })()"}' > /dev/null
sleep 3

echo "Threads bio updated."
