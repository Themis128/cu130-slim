"""LinkedIn advanced products — Lead Sync, Conversions API, Matched Audiences.

Behind ``LINKEDIN_ADVANCED=1`` until the access requests on 265077368 +
264925843 are granted (all submitted 2026-11-05). No-ops gracefully when
flagged off or token missing.
"""

import os

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

REST = "https://api.linkedin.com/rest"
V2 = "https://api.linkedin.com/v2"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}

ENABLED = os.getenv("LINKEDIN_ADVANCED", "0") == "1"


async def _ctx() -> tuple[str, str] | None:
    if not ENABLED:
        return None
    async with task_session() as db:
        acc = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == "linkedin",
                    SocialAccount.account_type == "organization",
                )
            )
        ).scalars().first()
        if not acc or not acc.access_token_enc:
            return None
        return decrypt_token(bytes(acc.access_token_enc)), f"urn:li:organization:{acc.account_id}"


# ── Lead Sync ────────────────────────────────────────────────────────────


async def lead_form_responses(form_urn: str) -> dict:
    """Pull responses for a lead-gen form — Standard lead-sync scope."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/leadFormResponses", params={"leadType": "urn:li:leadForm:" + form_urn.split(":")[-1]})
        return {"responses": (r.json().get("elements") or []) if r.status_code == 200 else [], "status": r.status_code}


# ── Conversions API ──────────────────────────────────────────────────────


async def stream_conversion(
    *,
    conversion_rule: str,
    happened_at_ms: int,
    email_sha256: str | None = None,
    lead_id: str | None = None,
) -> dict:
    """Stream a server-side conversion event (``audit_booked``, ``lead_created``)."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    user: dict = {}
    if email_sha256:
        user["email"] = {"sha256": email_sha256}
    if lead_id:
        user["leadId"] = lead_id
    payload = {
        "conversion": conversion_rule,
        "conversionHappenedAt": happened_at_ms,
        "user": user,
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/conversionEvents", json=payload)
        return {"status": r.status_code, "ok": r.status_code in (200, 201)}


# ── Matched Audiences ────────────────────────────────────────────────────


async def create_dmp_segment(name: str, description: str = "") -> dict:
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    payload = {"name": name, "description": description, "type": "USER"}
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/dmpSegments", json=payload)
        if r.status_code in (200, 201):
            return {"urn": r.headers.get("x-restli-id") or r.json().get("id")}
        return {"error": r.status_code, "body": r.text[:300]}


async def upload_dmp_users(segment_urn: str, users: list[dict]) -> dict:
    """Attach hashed users ({sha256_email} or {firstName,lastName,...}) to a segment."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    payload = {"elements": [{"action": "ADD", "userIds": u} for u in users]}
    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{V2}/dmpSegments/{segment_urn.split(':')[-1]}/users", json=payload)
        return {"status": r.status_code, "ok": r.status_code in (200, 201, 204)}
