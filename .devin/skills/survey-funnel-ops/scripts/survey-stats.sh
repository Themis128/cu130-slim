#!/usr/bin/env bash
# survey-stats.sh — survey-funnel health: recent survey leads + count
set -eu
cd "$(dirname "$0")/../../../.."
docker compose exec -T social-api python -c "
import asyncio
from app.services.espocrm_client import espo_get
async def m():
    r = await espo_get('/Lead', params={'maxSize':10,'orderBy':'createdAt','order':'desc','where':[{'attribute':'description','type':'contains','value':'survey'}]})
    ls = r.get('list',[])
    print(f'{len(ls)} survey leads (last 10):')
    for l in ls: print(' ', l.get('name'),'|',l.get('status'))
asyncio.run(m())"
