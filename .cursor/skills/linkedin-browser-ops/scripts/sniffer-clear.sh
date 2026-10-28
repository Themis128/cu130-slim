#!/usr/bin/env bash
# Clear the sniffer capture buffer.
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python /dev/stdin <<'PYEOF'
import asyncio, httpx, os
async def m():
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    async with httpx.AsyncClient(base_url=url, timeout=30) as c:
        await c.post("/debug/eval", json={"expr": "sessionStorage.removeItem('__sniff'); return 'cleared';"})
        print("cleared")
asyncio.run(m())
PYEOF
