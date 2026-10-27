#!/usr/bin/env bash
# x_reauth.sh — X bearer re-auth. Pulls client creds from env/secret-store
# and runs the wire flow; you only approve in the browser.
set -e
CID="${X_CLIENT_ID:-${TWITTER_CLIENT_ID:-}}"; SEC="${X_CLIENT_SECRET:-${TWITTER_CLIENT_SECRET:-}}"
if [ -z "$CID" ] || [ -z "$SEC" ]; then
  docker compose exec -T social-api python -c "
from app.services.secret_store import secret_store
import asyncio
async def g():
    print(await secret_store.get('X_CLIENT_ID') or await secret_store.get('TWITTER_CLIENT_ID') or '')
    print(await secret_store.get('X_CLIENT_SECRET') or await secret_store.get('TWITTER_CLIENT_SECRET') or '')
asyncio.run(g())" > /tmp/xcreds 2>/dev/null
  CID=$(sed -n 1p /tmp/xcreds); SEC=$(sed -n 2p /tmp/xcreds)
fi
[ -z "$CID" ] || [ -z "$SEC" ] && { echo "need X_CLIENT_ID + X_CLIENT_SECRET in env or secret store"; exit 1; }
python3 "$(dirname "$0")/../../../.devin/skills/x-oauth-wire/scripts/wire.py" "$CID" "$SEC"
