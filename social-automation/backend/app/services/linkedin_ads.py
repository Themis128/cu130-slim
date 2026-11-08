"""LinkedIn Ads API — account + campaign analytics pulls.

Tier upgrade verified 2026-10-26 (r_ads + r_ads_reporting live,
adAccountsV2 returns the real account). Pulls campaign stats into
meta_data for the analytics surface.
"""

import logging
from urllib.parse import quote

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)
API = "https://api.linkedin.com/v2"
REST = "https://api.linkedin.com/rest"
HEADERS_EXTRA = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}


def _urn(urn_or_id) -> str:
    """Percent-encode a URN for embedding in a Rest.li finder value.

    LinkedIn rejects literal ``:`` inside finder DSL values
    (``ILLEGAL_ARGUMENT``) but requires the ``()``/``List()`` syntax to stay
    unencoded — so encode the URN, keep the DSL literal.
    """
    s = str(urn_or_id or "")
    return s if s.startswith("urn:") else f"urn:li:sponsoredAccount:{s}"


def _enc_urn(urn_or_id) -> str:
    return quote(_urn(urn_or_id), safe="")


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
        r = await c.get(f"{API}/adAccountsV2?q=search&search=(status:(values:List(ACTIVE)))", headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA})
        r.raise_for_status()
        return r.json().get("elements", [])


async def campaigns(db, account_urn: str) -> list[dict]:
    """List campaigns under an ad account via the account-scoped path.

    ``adCampaigns?q=search&search=(account:...)`` is rejected (``account`` is
    not a searchable field); the ``/adAccounts/{id}/adCampaigns`` finder is
    the documented route and was verified live 2026-11-07.
    """
    tok = await _token(db)
    acct_id = str(account_urn).rstrip("}").split(":")[-1]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{REST}/adAccounts/{acct_id}/adCampaigns?q=search&search=(status:(values:List(ACTIVE,PAUSED)))",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA},
        )
        if r.status_code != 200:
            logger.warning("linkedin campaigns fetch failed: %s %s", r.status_code, getattr(r, "text", "")[:200])
            return []
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
            out["campaigns"].append(
                {
                    "id": cp.get("id"),
                    "name": cp.get("name", "")[:80],
                    "status": cp.get("status"),
                    "account": a.get("name", urn)[-40:],
                }
            )
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
            f"{REST}/leadForms?q=owner&owner=(sponsoredAccount:{_enc_urn(account_urn)})&count=10",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA},
        )
        if r.status_code != 200:
            logger.warning("linkedin leadForms fetch failed: %s %s", r.status_code, getattr(r, "text", "")[:200])
            return []
        return r.json().get("elements", [])


async def lead_form_responses(db, account_urn: str) -> list[dict]:
    """Pull actual lead submissions via the owner finder (verified 2026-11-07)."""
    tok = await _token(db)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{REST}/leadFormResponses?q=owner&owner=(sponsoredAccount:{_enc_urn(account_urn)})&count=50&leadType=(leadType:SPONSORED)",
            headers={"Authorization": f"Bearer {tok}", **HEADERS_EXTRA},
        )
        if r.status_code != 200:
            logger.warning("linkedin leadFormResponses fetch failed: %s %s", r.status_code, getattr(r, "text", "")[:200])
            return []
        return r.json().get("elements", [])
