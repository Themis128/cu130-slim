#!/usr/bin/env bash
# Navigate the sidecar's LinkedIn page. usage: nav.sh 'https://linkedin.com/feed/'
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - "$1" <<'PYEOF'
import asyncio, httpx, os, sys
async def m(url):
    s = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    async with httpx.AsyncClient(base_url=s, timeout=60) as c:
        r = await c.post("/debug/navigate", json={"url": url})
        print(r.status_code, r.text[:300])
asyncio.run(m(sys.argv[1]))
PYEOF
