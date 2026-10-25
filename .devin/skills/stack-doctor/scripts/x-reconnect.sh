#!/usr/bin/env bash
# x-reconnect.sh — generates the X OAuth2 reconnect URL for a dead token.
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
