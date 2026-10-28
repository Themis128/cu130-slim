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


async def _token(db, team_id=None):
    from app.core.security import decrypt_token
    from app.models.social_account import SocialAccount
    q = select(SocialAccount).where(SocialAccount.platform == "linkedin")
    if team_id is not None:
        q = q.where(SocialAccount.team_id == team_id)
    acc = (await db.execute(q)).scalars().first()
    return decrypt_token(acc.access_token_enc)


async def ad_accounts(db, team_id=None) -> list[dict]:
    tok = await _token(db, team_id)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/adAccountsV2?q=search&search=(status:(values:List(ACTIVE)))",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        r.raise_for_status()
        return r.json().get("elements", [])


async def campaigns(db, account_urn: str) -> list[dict]:
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/adCampaignsV2?q=search&search=(account:(values:List({account_urn})),"
            f"status:(values:List(ACTIVE,PAUSED)))",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        r.raise_for_status()
        return r.json().get("elements", [])


async def snapshot(db, team_id=None) -> dict:
    """Pull ad account + campaign state → meta_data on the account."""
    from app.models.social_account import SocialAccount
    accs = await ad_accounts(db, team_id)
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
    q = select(SocialAccount).where(SocialAccount.platform == "linkedin")
    if team_id is not None:
        q = q.where(SocialAccount.team_id == team_id)
    acc = (await db.execute(q)).scalars().first()
    if acc:
        acc.meta_data = {**(acc.meta_data or {}), "ads": out}
    return out


async def lead_gen_forms(db, account_urn: str) -> list[dict]:
    """Pull form definitions for an account (templates, not submissions)."""
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/leadGenForms?q=owner&owner=(sponsoredAccount:{account_urn})"
            "&count=10",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        r.raise_for_status()
        return r.json().get("elements", [])


async def lead_form_responses(db, account_urn: str) -> list[dict]:
    """Pull actual lead submissions via leadFormResponses finder."""
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/leadFormResponses?q=owner&owner=(sponsoredAccount:{account_urn})"
            "&count=50&leadType=(leadType:SPONSORED)",
            headers={"Authorization": f"Bearer {tok}",
                     "Linkedin-Version": "202404", **HEADERS_EXTRA})
        if r.status_code != 200:
            return []
        return r.json().get("elements", [])
