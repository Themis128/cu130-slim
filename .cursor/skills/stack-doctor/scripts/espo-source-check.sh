#!/usr/bin/env bash
# print the valid EspoCRM Lead `source` enum values
set -eu
cd /mnt/c/Users/tbaltzakis/cu130-slim
docker compose exec -T social-api python -c "
import asyncio
async def m():
    from app.services.espocrm_client import espo_get
    r = await espo_get('/Lead', params={'maxSize':20,'select':['source']})
    print(sorted({l['source'] for l in r.get('list',[]) if l.get('source')}))
asyncio.run(m())"
