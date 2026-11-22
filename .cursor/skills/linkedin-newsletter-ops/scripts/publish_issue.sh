#!/bin/bash
# publish_issue.sh — fire the staged LinkedIn newsletter issue via the
# newsletter bridge. Staged payload at /tmp/nl3_payload.json (in social-api).
# Usage: publish_issue.sh   (fired by cron at the circuit-open time)
docker exec -i social-api python - <<'PY'
import asyncio,sys,json; sys.path.insert(0,'/app')
from app.core.config import get_settings
import httpx
async def go():
    payload = json.loads(open('/tmp/nl3_payload.json').read())
    tok = get_settings().NEWSLETTER_SERVICE_TOKEN
    r = await httpx.AsyncClient(timeout=240).post(
        'http://localhost:8000/api/v1/newsletter/linkedin/issue',
        json=payload, headers={'X-Service-Token': tok})
    print(r.status_code, r.text[:300])
asyncio.run(go())
PY
