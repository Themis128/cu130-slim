"""LinkedIn Events Management API — org events for cloudless-gr page.

Standard Tier product (provisioned on 265077368 + 264925843). Creates org
events + returns the URN for scheduled reminder posts via publish_queue.
"""

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}


async def _org_token() -> tuple[str, str] | None:
    """(token, org_urn) for the LinkedIn organization account."""
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
        if not acc or not acc.access_token_enc:
            return None
        return decrypt_token(bytes(acc.access_token_enc)), f"urn:li:organization:{acc.account_id}"


async def create_org_event(
    name: str,
    starts_at_ms: int,
    ends_at_ms: int,
    *,
    description: str = "",
    event_url: str | None = None,
) -> dict:
    """Create a LinkedIn org event. Returns the created entity or error."""
    ctx = await _org_token()
    if not ctx:
        return {"skip": "no-linkedin-org-account"}
    token, org = ctx
    payload: dict = {
        "organizer": org,
        "name": {"localized": {"en_US": name}},
        "description": {"localized": {"en_US": description}} if description else None,
        "timeRange": {"start": starts_at_ms, "end": ends_at_ms},
        "format": "VIRTUAL",
    }
    if event_url:
        payload["virtualEventUrl"] = event_url
    payload = {k: v for k, v in payload.items() if v is not None}
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/events", json=payload)
        if r.status_code in (200, 201):
            body = r.json() if r.text else {}
            urn = r.headers.get("x-restli-id") or body.get("id") or body.get("entity")
            return {"urn": urn, "status": r.status_code}
        return {"error": r.status_code, "body": r.text[:300]}


async def list_org_events() -> dict:
    ctx = await _org_token()
    if not ctx:
        return {"skip": "no-linkedin-org-account"}
    token, org = ctx
    import urllib.parse

    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/events", params={"q": "organizer", "organizer": urllib.parse.quote(org)})
        return {"status": r.status_code, "events": (r.json().get("elements") or []) if r.status_code == 200 else []}
