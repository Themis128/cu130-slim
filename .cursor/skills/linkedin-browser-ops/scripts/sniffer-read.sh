#!/usr/bin/env bash
# Read captured requests. usage: sniffer-read.sh [regex] — optional URL filter
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python /dev/stdin "${1:-}" <<'PYEOF'
import asyncio, httpx, os, sys, json
async def m(rx):
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    expr = f"""const log=JSON.parse(sessionStorage.getItem('__sniff')||'[]');
const rx={json.dumps(rx)}; return rx?log.filter(x=>new RegExp(rx,'i').test(x.url)):log.slice(-20);"""
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:
        r = await c.post("/debug/eval", json={"expr": expr})
        print(json.dumps(r.json().get("value"), indent=1)[:4000])
asyncio.run(m(sys.argv[1] if len(sys.argv)>1 else ""))
PYEOF
