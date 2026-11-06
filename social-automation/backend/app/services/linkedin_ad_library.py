"""LinkedIn Ad Library — public ad-search API for intel (own + competitors).

Provisioned on 264925843 + 227354605. Pulls ads by advertiser/keyword into a
competitor-intel surface used by strategy briefs.
"""

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}


async def _token() -> str | None:
    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        return decrypt_token(bytes(acc.access_token_enc)) if acc and acc.access_token_enc else None


async def search_ads(
    *,
    advertiser: str | None = None,
    keyword: str | None = None,
    country: str = "GR",
    limit: int = 25,
) -> dict:
    """Search the Ad Library. ``advertiser`` is a page name or org URN."""
    token = await _token()
    if not token:
        return {"skip": "no-linkedin-token"}
    params: dict = {"countries": f"List({country})", "count": limit}
    if advertiser:
        params["advertisers"] = f"List({advertiser})"
    if keyword:
        params["keyword"] = keyword
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/adLibrary", params=params)
        if r.status_code == 200:
            return {"ads": r.json().get("elements") or []}
        return {"error": r.status_code, "body": r.text[:300]}
