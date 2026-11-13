#!/usr/bin/env bash
# mint_authorize.sh <platform> [container] — mint an OAuth authorize URL
# platform: facebook|instagram-onboarding|threads|tiktok|whatsapp|linkedin|twitter|bluesky
# Run on the Windows host or WSL; talks to the API container via docker exec.
set -euo pipefail
PLATFORM="${1:?platform required}"; CTX="${2:-social-api-green}"
TEAM="10024d25-2ea4-40ea-86c6-ba89f515a2eb"
docker.exe exec "$CTX" python - "$PLATFORM" "$TEAM" <<'PY'
import sys, json, httpx, os
platform, team = sys.argv[1], sys.argv[2]
# fresh admin JWT inside the container (asyncpg-only image)
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import create_access_token
async def tok():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        uid = await c.scalar(text("select id from users order by created_at limit 1"))
        return create_access_token({"sub": str(uid)})
t = asyncio.run(tok())
r = httpx.get(f"http://localhost:8000/api/v1/auth/oauth/{platform}/authorize",
              params={"team_id": team}, headers={"Authorization": f"Bearer {t}"},
              timeout=30)
print(r.status_code, r.json().get("authorization_url", r.text)[:2000])
PY
