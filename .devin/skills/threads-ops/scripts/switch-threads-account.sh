#!/usr/bin/env bash
# Switch the VNC browser to a saved Instagram/Threads profile.
set -euo pipefail

TARGET=${1:-"cloudless.gr"}

cd /home/tbaltzakis/cu130-slim

echo "=== Switching to Instagram/Threads profile: $TARGET ==="

# Log out to clear the active Instagram/Threads session
curl -s -X POST http://localhost:9223/session/navigate \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.instagram.com/accounts/logout/"}' >/dev/null
sleep 4

# Bring up the saved profile picker
curl -s -X POST http://localhost:9223/session/navigate \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.instagram.com/accounts/login/"}' >/dev/null
sleep 6

# Build the evaluate JSON via Python to avoid shell escaping issues
python3 - <<PY | curl -s -X POST http://localhost:9223/session/evaluate \
  -H 'Content-Type: application/json' \
  -d @- >/tmp/click_result.json
target = """$TARGET"""
import json, os
expr = f"""(function(){{ var target = {json.dumps(target)}; var btns = Array.from(document.querySelectorAll('button, div[role=button]')).filter(function(b){{ return b.innerText && b.innerText.trim() === target; }}); if(btns.length >= 1){{ btns[0].click(); return 'clicked ' + btns.length + ' matches'; }} return 'not found'; }})()"""
print(json.dumps({'expression': expr}))
PY

CLICK_RESULT=$(python3 -c "import json; print(json.load(open('/tmp/click_result.json')).get('result',''))")
echo "Profile picker: $CLICK_RESULT"
sleep 8

# Report the current state
curl -s -X POST http://localhost:9223/session/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"expression":"JSON.stringify({url: document.location.href.substring(0,120), title: document.title, body: document.body.innerText.substring(0, 200)})"}' > /tmp/page_state.json

echo "Current page:"
python3 -c "import json; d=json.load(open('/tmp/page_state.json')); r=json.loads(d.get('result','{}')); print(json.dumps(r, indent=2, ensure_ascii=False))"

echo ""
echo "If the profile was not saved, open noVNC at http://localhost:6080/vnc.html and log in manually."
