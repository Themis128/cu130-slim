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


# ── Lead Sync ────────────────────────────────────────────────────────────


async def lead_form_responses(form_urn: str) -> dict:
    """Pull responses for a lead-gen form — Standard lead-sync scope."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    form_id = form_urn.split(":")[-1]
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(
            f"{REST}/leadFormResponses",
            params={"q": "leadType", "leadType": f"urn:li:leadForm:{form_id}", "count": 50},
        )
        if r.status_code != 200:
            return {"responses": [], "status": r.status_code}
        return {"responses": r.json().get("elements") or [], "status": 200}


# ── Conversions API ──────────────────────────────────────────────────────


async def stream_conversion(
    *,
    conversion_rule: str,
    happened_at_ms: int,
    email_sha256: str | None = None,
    lead_id: str | None = None,
) -> dict:
    """Stream a server-side conversion event (``audit_booked``, ``lead_created``).

    Verified live 2026-11-07: ``POST /rest/conversionEvents`` requires the
    ``X-RestLi-Method: BATCH_CREATE`` header, an ``elements`` batch wrapper,
    ``user.userIds[]`` identifiers, and a ``urn:lla:llaPartnerConversion:{id}``
    rule URN (``urn:li:conversion:*`` prefixes are rejected with 422).
    """
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    user: dict = {}
    if email_sha256:
        user["userIds"] = [{"idType": "SHA256_EMAIL", "idValue": email_sha256}]
    if lead_id:
        user["lead"] = lead_id if lead_id.startswith("urn:") else f"urn:li:leadGenFormResponse:{lead_id}"
    payload = {
        "elements": [
            {
                "conversion": conversion_rule,
                "conversionHappenedAt": happened_at_ms,
                "user": user,
            }
        ]
    }
    headers = {
        "Authorization": f"Bearer {token}",
        **HDRS,
        "Content-Type": "application/json",
        "X-RestLi-Method": "BATCH_CREATE",
    }
    async with httpx.AsyncClient(timeout=30, headers=headers) as c:
        r = await c.post(f"{REST}/conversionEvents", json=payload)
        out: dict = {"status": r.status_code, "ok": r.status_code in (200, 201)}
        try:
            body = r.json()
            if body.get("elements"):
                out["element"] = body["elements"][0]
        except ValueError:
            pass
        return out


# ── Matched Audiences ────────────────────────────────────────────────────


AD_ACCOUNT_URN = "urn:li:sponsoredAccount:512642510"


async def create_dmp_segment(name: str, description: str = "") -> dict:
    """Create a USER DMP segment. Schema per MS Learn matched-audiences:
    ``account`` + ``type`` + ``sourcePlatform`` are required."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    payload = {
        "name": name,
        "description": description,
        "account": AD_ACCOUNT_URN,
        "type": "USER",
        "sourcePlatform": "DIRECT_API",
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/dmpSegments", json=payload)
        if r.status_code in (200, 201):
            return {"urn": r.headers.get("x-restli-id") or r.json().get("id")}
        return {"error": r.status_code, "body": r.text[:300]}


async def upload_dmp_users(segment_urn: str, users: list[dict | list[dict]]) -> dict:
    """Attach users to a segment. ``users`` is a list of TypedId dicts
    (``{"idType": "SHA256_EMAIL", "idValue": ...}``) or profile-field dicts;
    a nested list becomes one element whose ``userIds`` carries the whole
    chunk — LinkedIn accepts up to 5000 ids per call."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    elements = []
    for u in users:
        ids = u if isinstance(u, list) else [u]
        elements.append({"action": "ADD", "userIds": ids})
    payload = {"elements": elements}
    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/dmpSegments/{segment_urn.split(':')[-1]}/users", json=payload)
        return {"status": r.status_code, "ok": r.status_code in (200, 201, 204)}
