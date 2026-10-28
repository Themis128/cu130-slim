#!/usr/bin/env bash
# In-page subscriber fetch — the pattern fetch_subscribers should adopt.
# usage: fetch-subscribers.sh 'urn:li:fsd_contentSeries:<id>' [start]
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - "$1" "${2:-0}" <<'PYEOF'
import asyncio, httpx, os, sys, json
async def m(urn, start):
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    expr = f"""const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/)||[])[1]||'';
const r = await fetch('/voyager/api/voyagerPublishingDashSeriesSubscribers?q=contentSeries&seriesUrn={urn}&count=50&start={start}',
  {{headers:{{'csrf-token':csrf,'x-restli-protocol-version':'2.0.0'}}}});
const t = await r.text();
return {{status: r.status, count: (t.match(/fsd_miniProfile/g)||[]).length, sample: t.slice(0,400)}};"""
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:
        r = await c.post("/debug/eval", json={"expr": expr})
        print(json.dumps(r.json().get("value", r.json()), indent=1)[:1400])
asyncio.run(m(sys.argv[1], sys.argv[2]))
PYEOF
