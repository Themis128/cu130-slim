"""linkedin_scope_audit.py — REAL granted-scope matrix for a LinkedIn token.

The social_accounts.scopes column records what was REQUESTED, not what was
GRANTED — LinkedIn silently drops unapproved scopes at consent. This tool
probes one endpoint per scope and reports the live grant matrix:

    granted    → endpoint returned 200
    denied     → 403 (scope not granted OR product not enabled on the app)
    missing    → 404/426 (endpoint product absent or dead version)
    no-token   → account row has no usable token

Run inside social-api (scripts bind-mount is :ro — cp to /tmp):

    docker compose cp social-automation/backend/scripts/linkedin_scope_audit.py \
      social-api:/tmp/sa.py
    docker compose exec -T -w /app social-api env PYTHONPATH=/app \
      python /tmp/sa.py 9c4451bb-e820-489f-8676-76ddbc788ffe
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount

V = "202610"
ORG = "urn:li:organization:108614163"
ORG_ID = "108614163"

# scope → (label, endpoint). One probe each; 200 = the scope/product is live.
E = "{ORG.replace(':', '%3A')}"

PROBES: list[tuple[str, str, str]] = [
    ("openid+profile", "userinfo",
     "https://api.linkedin.com/v2/userinfo"),
    ("w_organization_social", "org posts",
     f"https://api.linkedin.com/rest/posts?q=author&author={E}&count=1"),
    ("r_organization_social", "share stats",
     "https://api.linkedin.com/rest/organizationalEntityShareStatistics"
     f"?q=organizationalEntity&organizationalEntity={E}&count=1"),
    ("r_organization_admin", "org read",
     f"https://api.linkedin.com/rest/organizations/{ORG_ID}?fields=id"),
    ("rw_organization_admin", "org acls",
     "https://api.linkedin.com/rest/organizationAcls"
     f"?q=organization&organization={E}"),
    ("r_ads", "ad accounts",
     "https://api.linkedin.com/rest/adAccounts"
     "?q=search&search=(status:(values:List(ACTIVE)))&count=1"),
    ("r_organization_followers", "follower stats",
     "https://api.linkedin.com/rest/organizationalEntityFollowerStatistics"
     f"?q=organizationalEntity&organizationalEntity={E}&count=1"),
    ("r_member_postAnalytics", "member post analytics",
     "https://api.linkedin.com/rest/memberCreatorPostAnalytics"
     "?q=entity&entity=urn%3Ali%3Aperson%3ASIkIOg3i31"),
    ("r_member_profileAnalytics", "member snapshot",
     "https://api.linkedin.com/rest/memberSnapshotData"
     "?personUrn=urn%3Ali%3Aperson%3ASIkIOg3i31"),
    ("*_social_feed", "org feed comments",
     f"https://api.linkedin.com/rest/socialActions/{E}"
     "%3A7523840153971449856/comments?count=1"),
    ("r_1st_connections_size", "connections",
     "https://api.linkedin.com/rest/connections?q=count"),
]


async def audit(account_id: str) -> None:
    async with async_session_maker() as db:
        acc = (
            await db.execute(select(SocialAccount).where(SocialAccount.id == account_id))
        ).scalar_one_or_none()
        if not acc or not acc.access_token_enc:
            print("no-token")
            return
        db.expunge(acc)
    tok = decrypt_token(bytes(acc.access_token_enc))
    h = {"Authorization": f"Bearer {tok}", "Linkedin-Version": V,
         "X-Restli-Protocol-Version": "2.0.0"}
    print(f"account={account_id} updated={acc.updated_at}")
    async with httpx.AsyncClient(timeout=20) as c:
        for scope, label, url in PROBES:
            r = await c.get(url, headers=h)
            verdict = {200: "granted", 403: "denied", 429: "rate-limited"}.get(
                r.status_code, f"missing({r.status_code})")
            print(f"  {verdict:>10}  {scope:<28} {label}")


if __name__ == "__main__":
    asyncio.run(audit(sys.argv[1] if len(sys.argv) > 1 else
                      "9c4451bb-e820-489f-8676-76ddbc788ffe"))
