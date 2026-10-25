#!/usr/bin/env bash
# Is the linkedin sidecar session alive? (feeds the subscriber sync gate)
set -u
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF' 2>&1 | tail -6
import asyncio
async def m():
    from app.services.linkedin_sidecar import LinkedInSidecarClient
    c = LinkedInSidecarClient()
    try:
        h = await c.health(); print("health:", h)
    except Exception as e: print("health check failed:", e)
    try:
        s = await c.check_session(); print("session:", s)
    except Exception as e: print("session check failed:", e)
    try:
        ck = await c.export_cookies()
        print("cookies:", "li_at" if "li_at" in ck else "NO li_at",
              "| JSESSIONID" if "JSESSIONID" in ck else "NO JSESSIONID")
    except Exception as e: print("cookie export failed:", e)
asyncio.run(m())
PYEOF
