#!/usr/bin/env bash
# x-reconnect.sh — generates the X OAuth2 reconnect URL for a dead token.
<<<<<<< HEAD
# Logs in via /auth/login (OAuth2 form data — NOT json), then POSTs
# /connect/x for the authorize URL.
set -eu
cd "$(dirname "$0")/../../../.."
BASE="${API_BASE:-http://localhost:8083}"
get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d "'" | tr -d '"'; }
EMAIL=$(get SOCIAL_ADMIN_EMAIL); PASS=$(get SOCIAL_ADMIN_PASSWORD)
[ -z "$EMAIL" ] && EMAIL="admin@example.com"
TOKEN=$(curl -s -m10 -X POST "$BASE/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=$EMAIL&password=$PASS" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")
[ -z "$TOKEN" ] && { echo "login failed — check SOCIAL_ADMIN_EMAIL/PASSWORD"; exit 1; }
curl -s -m10 -X POST "$BASE/api/v1/accounts/connect/twitter?team_id=${TEAM_ID:-79c61b7d-5005-489d-a833-eabd94ecc870}" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('authorize_url', d))"
=======
# The app already implements /connect/{platform}; this surfaces it for
# the refresh-400 case (rotated/dead refresh token → full re-auth).
set -eu
cd /mnt/c/Users/tbaltzakis/cu130-slim
TOKEN=$(docker compose exec -T social-api python -c "
import asyncio
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User
from sqlalchemy import select
async def m():
    async for db in get_db():
        r = await db.execute(select(User).where(User.email=='admin@example.com'))
        u = r.scalar_one_or_none()
        if u: print(create_access_token(str(u.id)))
asyncio.run(m())" 2>/dev/null | tail -1)
curl -s -m10 -X POST "http://localhost:8083/api/v1/accounts/connect/x" -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('authorize_url', d))"
>>>>>>> 98881033 (feat(ops): x-reconnect.sh + metabase-stale-queries runbook)
