#!/usr/bin/env bash
# Live funnel: subscribers synced → CRM leads → audits → proposals.
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF' 2>&1 | tail -8
import asyncio
from sqlalchemy import text
from app.db.session import async_session_maker
async def m():
    async with async_session_maker() as db:
        q = lambda t: db.execute(text(t))
        subs = (await q("SELECT count(*) FROM leads WHERE meta_data->>'form'='linkedin-newsletter' OR notes LIKE '%linkedin newsletter%'")).scalar()
        audits = (await q("SELECT count(*) FROM agency_audits WHERE meta_data->>'source'='public-audit'")).scalar()
        props = dict((await q("SELECT status, count(*) FROM agency_proposals GROUP BY 1")).all())
        print(f"newsletter-linked leads: {subs}")
        print(f"public-audit runs: {audits}")
        print("proposals:", dict(props))
asyncio.run(m())
PYEOF
