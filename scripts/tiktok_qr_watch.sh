#!/usr/bin/env bash
# Watch the headed bridge (9223) for a completed TikTok QR login, then
# extract cookies and inject them into the tiktok sidecar (9224).
# Detection: real tiktok auth cookies (sessionid/sid_tt) — NOT url change.
# The tagged evaluate poll also refreshes the tiktok busy-hold so foreign
# platform pollers cannot preempt mid-login.
set -u
BRIDGE="http://localhost:9223"
SIDECAR="http://localhost:9224"
DEADLINE=$(( $(date +%s) + 1500 ))   # 25 min
cd "$HOME/cu130-slim" || exit 1

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  INFO=$(curl -s -m 15 -X POST "$BRIDGE/session/evaluate" \
    -H 'Content-Type: application/json' -H 'X-Platform: tiktok' \
    -d '{"expression":"JSON.stringify({url:location.href,title:document.title})"}' 2>/dev/null)
  echo "[$(date +%H:%M:%S)] $(echo "$INFO" | head -c 160)"

  if echo "$INFO" | grep -q 'tiktok.com' && ! echo "$INFO" | grep -qi 'login'; then
    EXT=$(curl -s -m 20 -X POST "$BRIDGE/session/extract" \
      -H 'Content-Type: application/json' -H 'X-Platform: tiktok' -d '{}' 2>/dev/null)
    echo "[$(date +%H:%M:%S)] extract: $(echo "$EXT" | head -c 200)"
    if echo "$EXT" | grep -qE '"sessionid"|"sid_tt"'; then
      echo "REAL LOGIN — injecting cookies into sidecar"
      echo "$EXT" > /tmp/tk_extract.json
      python3 - <<'PY'
import json, urllib.request
d = json.load(open('/tmp/tk_extract.json'))
cks = d.get('cookies', {})
body = json.dumps({'session_id': cks.get('sessionid',''), 'cookies': cks}).encode()
req = urllib.request.Request('http://localhost:9224/session', data=body,
                             headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req, timeout=60).read().decode())
PY
      exit 0
    fi
  fi
  sleep 25
done
echo "TIMEOUT — QR expired or never scanned"
exit 1
