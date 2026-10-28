#!/usr/bin/env bash
# Subscribers synced this week — the welcome-touch checklist.
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF' 2>&1 | tail -15
import asyncio
import redis.asyncio as aioredis
from app.core.config import get_settings
async def m():
    r = aioredis.from_url(get_settings().REDIS_URL)
    urns = await r.smembers("linkedin-newsletter:synced-urns")
    print(f"{len(urns)} subscriber URNs synced total")
    for u in list(urns)[:15]:
        print(" ", u.decode() if isinstance(u, bytes) else u)
asyncio.run(m())
PYEOF
