#!/usr/bin/env bash
# LinkedIn page specialties via the Organization API — replaces the manual
# click. localizedSpecialties is an admin-writable field (Admin Only in the
# Organization schema); needs scope rw_organization_admin on the app token.
#   ./linkedin-specialties.sh [ORG_ID]
# ORG_ID defaults to the company-page org id used by publishing.
set -eu
ORG_ID="${1:-${LINKEDIN_ORG_ID:-}}"
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - "$ORG_ID" <<'PYEOF'
import asyncio, sys, os
org_id = sys.argv[1] or os.environ.get("LINKEDIN_ORG_ID")
WANTED = ["Web Development","Managed Hosting","AI Automation","Custom Software","Digital Marketing"]
async def main():
    from app.services.linkedin_api import LinkedInAPIClient, LINKEDIN_REST_BASE, LINKEDIN_V2_BASE
    import httpx
    token = None
    try:
        from sqlalchemy import text
        from app.db.session import async_session_maker
        async with async_session_maker() as db:
            row = (await db.execute(text("SELECT access_token_enc FROM social_accounts WHERE platform='linkedin' AND access_token_enc IS NOT NULL AND status='active' ORDER BY created_at DESC LIMIT 1"))).first()
            token = row[0] if row else None
    except Exception as e:
        print('token lookup failed:', e)
    if not token:
        token = __import__('os').environ.get('LINKEDIN_ACCESS_TOKEN')
    if not token:
        print('no linkedin token found (social_accounts + env) — connect the account first'); return
    c = LinkedInAPIClient(access_token=token)
    if not org_id:
        orgs = await c.get_member_organizations()
        print("admin orgs:", [(o.id, o.vanity_name or o.name) for o in orgs])
        return
    headers = {**c._headers(), "Content-Type": "application/json", "X-Restli-Protocol-Version": "2.0.0"}
    body = {"localizedSpecialties": WANTED}
    async with httpx.AsyncClient(timeout=20) as http:
        for url in (f"{LINKEDIN_REST_BASE}/organizations/{org_id}", f"{LINKEDIN_V2_BASE}/organizations/{org_id}"):
            r = await http.patch(url, headers=headers, json=body)
            print(r.status_code, url)
            if r.status_code in (200, 204):
                print("specialties set:", WANTED); return
            if r.status_code == 403:
                print("403 — token lacks rw_organization_admin (scope request needed); manual edit remains")
                return
asyncio.run(main())
PYEOF
