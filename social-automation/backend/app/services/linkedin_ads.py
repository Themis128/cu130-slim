"""LinkedIn Ads API — account + campaign analytics pulls.

Tier upgrade verified 2026-10-26 (r_ads + r_ads_reporting live,
adAccountsV2 returns the real account). Pulls campaign stats into
meta_data for the analytics surface.
"""
import logging

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)
API = "https://api.linkedin.com/v2"
HEADERS_EXTRA = {"X-Restli-Protocol-Version": "2.0.0"}


async def _token(db):
    from app.core.security import decrypt_token
    from app.models.social_account import SocialAccount
    acc = (await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "linkedin"))
    ).scalars().first()
    return decrypt_token(acc.access_token_enc)


async def ad_accounts(db) -> list[dict]:
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/adAccountsV2?q=search&search=(status:(values:List(ACTIVE)))",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        return r.json().get("elements", [])


async def campaigns(db, account_urn: str) -> list[dict]:
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/adCampaignsV2?q=search&search=(status:(values:List(ACTIVE,PAUSED)))"
            f"&search=(account:(values:List({account_urn})))",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        return r.json().get("elements", [])


async def snapshot(db) -> dict:
    """Pull ad account + campaign state → meta_data on the account."""
    from app.models.social_account import SocialAccount
    accs = await ad_accounts(db)
    if not accs:
        return {"accounts": 0}
    out = {"accounts": len(accs), "campaigns": []}
    for a in accs[:3]:
        urn = a.get("urn", f"urn:li:sponsoredAccount:{a.get('id')}")
        camps = await campaigns(db, urn)
        for cp in camps:
            out["campaigns"].append({
                "id": cp.get("id"),
                "name": cp.get("name", "")[:80],
                "status": cp.get("status"),
                "account": a.get("name", urn)[-40:],
            })
    acc = (await db.execute(
        select(SocialAccount).where(SocialAccount.platform == "linkedin"))
    ).scalars().first()
    if acc:
        acc.meta_data = {**(acc.meta_data or {}), "ads": out}
    return out


async def lead_gen_forms(db, account_urn: str) -> list[dict]:
    """Pull Lead Gen Form responses — the CRM loop endpoint.

    Each form response carries lead fields (name, email, company) that
    map straight into the CRM contacts path.
    """
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/leadGenForms?q=owner&owner=(sponsoredAccount:{account_urn})"
            "&count=10",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        return r.json().get("elements", [])
