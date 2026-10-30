#!/usr/bin/env bash
set -euo pipefail
SIDECAR="${LINKEDIN_SIDECAR:-http://localhost:9225}"
AUTH_URL=$(docker exec social-worker-media python3 -c "
import asyncio,os
async def go():
    from app.services.secret_store import secret_store
    cid=await secret_store.get('LINKEDIN_CLIENT_ID')
    redir=os.environ.get('LINKEDIN_REDIRECT_URI','https://social.cloudless.gr/api/v1/auth/oauth/linkedin/callback')
    from app.api.auth import _linkedin_scopes
    sc='%20'.join(_linkedin_scopes())
    print(f'https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id={cid}&redirect_uri={redir}&scope={sc}')
asyncio.run(go())" | tail -1)
echo "authorize: ${AUTH_URL:0:100}..."
curl -s -X POST "$SIDECAR/debug/navigate" -H 'Content-Type: application/json' -d "{\"url\":\"$AUTH_URL\"}" >/dev/null
sleep 10
curl -s -X POST "$SIDECAR/debug/eval" -H 'Content-Type: application/json' -d '{"expr":"(()=>{const b=[...document.querySelectorAll(\"button\")].find(x=>/allow|authorize/i.test(x.innerText||\"\"));if(b){b.click();return \"clicked\"}return document.body.innerText.slice(0,150)})()"}'
sleep 4
curl -s -X POST "$SIDECAR/debug/eval" -H 'Content-Type: application/json' -d '{"expr":"location.href"}'
