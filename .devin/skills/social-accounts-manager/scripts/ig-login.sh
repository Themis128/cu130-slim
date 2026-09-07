#!/usr/bin/env bash
# Login to Instagram sidecar (tries saved sessionids, then username/password)
# Usage: ig-login.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="http://localhost:8011"

echo "→ Attempting Instagram login..."

# Try saved sessions first
SESSIONIDS=$(docker compose exec -T instagram-private-api python3 -c "
import json
with open('/data/db.json') as f:
    d = json.load(f)
sessions = d.get('_default', {})
if isinstance(sessions, dict):
    for k, v in sorted(sessions.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=True):
        if isinstance(v, dict) and 'sessionid' in v:
            sid = v['sessionid']
            if sid.startswith('36910035385'):
                print(sid)
" 2>/dev/null)

if [ -n "$SESSIONIDS" ]; then
  echo "  Found saved sessions, trying them..."
  echo "$SESSIONIDS" | while IFS= read -r SID; do
    [ -z "$SID" ] && continue
    echo "  Trying: ${SID:0:30}..."
    RESP=$(curl -sf -X POST "$API/auth/login/by/sessionid" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "sessionid=$SID" 2>/dev/null || echo "")
    if [ -n "$RESP" ] && echo "$RESP" | grep -q "session_id\|sessionid"; then
      SIDECAR_SID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null)
      if [ -n "$SIDECAR_SID" ]; then
        echo "$SIDECAR_SID" > /tmp/ig-sidecar-session.txt
        echo "  ✓ Login successful! Session: ${SIDECAR_SID:0:30}..."
        exit 0
      fi
    fi
  done
fi

# Try username/password
echo "  No saved sessions worked, trying username/password..."
CREDS=$(docker compose exec -T social-api python3 -c "
import asyncio
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text(\"SELECT key, value FROM social_secrets WHERE key IN ('INSTAGRAM_USERNAME', 'INSTAGRAM_PASSWORD')\"))
        creds = {row[0]: row[1] for row in result.fetchall()}
        print(f\"{creds.get('INSTAGRAM_USERNAME','')}\t{creds.get('INSTAGRAM_PASSWORD','')}\")

asyncio.run(main())
" 2>/dev/null | tail -1)

USERNAME=$(echo "$CREDS" | cut -f1)
PASSWORD=$(echo "$CREDS" | cut -f2)

if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "  ERROR: No Instagram credentials found in secret store."
  exit 1
fi

RESP=$(curl -sf -X POST "$API/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$USERNAME" \
  --data-urlencode "password=$PASSWORD" \
  --data-urlencode "locale=el_GR" \
  --data-urlencode "timezone=10800" \
  --data-urlencode "proxy=socks5://warp-proxy:1080" 2>/dev/null || echo "")

if echo "$RESP" | grep -q "session_id\|sessionid"; then
  SIDECAR_SID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null)
  echo "$SIDECAR_SID" > /tmp/ig-sidecar-session.txt
  echo "  ✓ Login successful! Session: ${SIDECAR_SID:0:30}..."
else
  echo "  ✗ Login failed: $(echo "$RESP" | head -c 200)"
  echo "  Try: bash .devin/skills/instagram-profile-manager/scripts/login-via-facebook.sh"
  exit 1
fi
