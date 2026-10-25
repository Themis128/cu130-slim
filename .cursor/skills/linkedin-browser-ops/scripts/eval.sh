#!/usr/bin/env bash
# Run a JS expression in the sidecar's live LinkedIn page.
# usage: eval.sh 'return location.href;'
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - "$1" <<'PYEOF'
import asyncio, httpx, os, sys, json
async def m(expr):
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:
        r = await c.post("/debug/eval", json={"expr": expr})
        print(json.dumps(r.json(), indent=1)[:2000])
asyncio.run(m(sys.argv[1]))
PYEOF
