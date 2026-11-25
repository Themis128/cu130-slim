"""LinkedIn advanced products — Lead Sync, Conversions API, Matched Audiences.

Behind ``LINKEDIN_ADVANCED=1`` until the access requests on 265077368 +
264925843 are granted (all submitted 2026-11-05). No-ops gracefully when
flagged off or token missing.
"""

import logging
import os
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

REST = "https://api.linkedin.com/rest"
V2 = "https://api.linkedin.com/v2"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}

ENABLED = os.getenv("LINKEDIN_ADVANCED", "0") == "1"

logger = logging.getLogger(__name__)


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


async def get_lead_form(form_id: str) -> dict | None:
    """``GET /rest/leadForms/{id}`` — form definition incl. question map.

    ``form_id`` accepts a bare id, ``urn:li:leadGenForm:N`` or a
    ``urn:li:versionedLeadGenForm:(urn:li:leadGenForm:N,V)`` URN.
    """
    import re

    m = re.search(r"leadGenForm:(\d+)", str(form_id))
    fid = m.group(1) if m else str(form_id)
    ctx = await _ctx()
    if not ctx:
        return None
    token, _ = ctx
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/leadForms/{fid}")
    if r.status_code != 200:
        logger.info("lead form %s fetch → %s", fid, r.status_code)
        return None
    return r.json()


def lead_form_question_map(form: dict | None) -> dict[int, dict]:
    """``content.questions[]`` → ``{questionId: {keys, label, options}}``.

    Each entry exposes ``keys`` (name + predefinedField, lowercased) for
    answer lookup and ``options`` (ordered ``[{id, text}]``) for
    multiple-choice resolution.
    """
    out: dict[int, dict] = {}
    for q in ((form or {}).get("content") or {}).get("questions") or []:
        qid = q.get("questionId")
        if qid is None:
            continue
        loc = ((q.get("question") or {}).get("localized")) or {}
        label = loc.get("en_US") or next(iter(loc.values()), "")
        keys = {str(k).lower() for k in (q.get("name"), q.get("predefinedField")) if k}
        options: list[dict] = []
        mc = ((q.get("questionDetails") or {}).get("multipleChoiceQuestionDetails") or {}).get("options") or []
        for opt in mc:
            otext = ((opt.get("text") or {}).get("localized")) or {}
            olabel = otext.get("en_US") or next(iter(otext.values()), "")
            options.append({"id": opt.get("id"), "text": olabel})
        out[int(qid)] = {"keys": keys, "label": label, "options": options}
    return out


async def list_lead_notifications(owner_urn: str, lead_type: str = "SPONSORED") -> dict:
    """Existing lead-notification subscriptions for an owner.

    ``GET /rest/leadNotifications?q=criteria&owner=(value:…)&leadType=(leadType:…)``
    """
    ctx = await _ctx()
    if not ctx:
        return {"notifications": [], "skip": "disabled-or-no-account"}
    token, _ = ctx
    owner_key = "sponsoredAccount" if "sponsoredAccount" in owner_urn else "organization"
    # Rest.li union params must be pre-encoded in the URL — httpx params=
    # would double-encode the %XX sequences (verified live 2026-11-15).
    enc = quote(owner_urn, safe="")
    url = f"{REST}/leadNotifications?q=criteria&owner=(value:({owner_key}:{enc}))&leadType=(leadType:{lead_type})"
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(url)
    if r.status_code != 200:
        return {"notifications": [], "status": r.status_code}
    return {"notifications": r.json().get("elements") or [], "status": 200}


async def create_lead_notification(webhook_url: str, owner_urn: str, lead_type: str = "SPONSORED") -> dict:
    """Subscribe a webhook to lead notifications — ``POST /rest/leadNotifications``.

    Owner level only (recommended by LinkedIn). ``owner_urn`` is a
    ``urn:li:sponsoredAccount:`` or ``urn:li:organization:`` URN; the owner
    key is derived from the URN prefix.
    """
    ctx = await _ctx()
    if not ctx:
        return {"error": "disabled-or-no-account"}
    token, _ = ctx
    owner_key = "sponsoredAccount" if "sponsoredAccount" in owner_urn else "organization"
    payload = {
        "webhook": webhook_url,
        "owner": {owner_key: owner_urn},
        "leadType": lead_type,
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/leadNotifications", json=payload)
    if r.status_code not in (200, 201):
        return {"error": r.status_code, "body": r.text[:300]}
    out: dict = {"ok": True, "status": r.status_code, "owner": owner_urn, "leadType": lead_type}
    if r.headers.get("x-restli-id"):
        out["id"] = r.headers["x-restli-id"]
    return out


# ── Conversions API ──────────────────────────────────────────────────────


async def stream_conversion(
    *,
    conversion_rule: str,
    happened_at_ms: int,
    email_sha256: str | None = None,
    lead_id: str | None = None,
    conversion_value: float | None = None,
    currency_code: str | None = None,
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
    element: dict = {
        "conversion": conversion_rule,
        "conversionHappenedAt": happened_at_ms,
        "user": user,
    }
    if conversion_value is not None:
        element["conversionValue"] = conversion_value
    if currency_code:
        element["currencyCode"] = currency_code
    payload = {"elements": [element]}
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


# ── Conversion rules (create/list/resolve) ───────────────────────────────

# event name → (env var holding the rule URN, LinkedIn conversion `type`)
# ``type`` is the Campaign Manager "key conversion behavior". The API enum
# only accepts LEAD / CONTACT / PURCHASE (verified live 2026-11-16 —
# SIGNUP and CUSTOM 422 "not an enum symbol"), so micro-conversions map to
# the closest accepted bucket.
CONVERSION_EVENTS: dict[str, tuple[str, str]] = {
    "audit_booked": ("LINKEDIN_CONVERSION_RULE", "LEAD"),
    "lead_created": ("LINKEDIN_CONVERSION_RULE_LEAD_CREATED", "LEAD"),
    "contact_created": ("LINKEDIN_CONVERSION_RULE_CONTACT", "LEAD"),
    "consultation_booked": ("LINKEDIN_CONVERSION_RULE_CONSULT", "LEAD"),
    "signup": ("LINKEDIN_CONVERSION_RULE_SIGNUP", "LEAD"),
    "purchase": ("LINKEDIN_CONVERSION_RULE_PURCHASE", "PURCHASE"),
    # Closed-loop: EspoCRM lead reached Converted — downstream optimization
    # signal so LinkedIn bids toward leads that close, not just form fills.
    "lead_won": ("LINKEDIN_CONVERSION_RULE_WON", "CONTACT"),
}


async def list_conversion_rules() -> dict:
    """All conversion rules on the ads account — ``GET /rest/conversions``."""
    ctx = await _ctx()
    if not ctx:
        return {"rules": [], "skip": "disabled-or-no-account"}
    token, _ = ctx
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/conversions", params={"q": "account", "account": AD_ACCOUNT_URN, "count": 100})
    if r.status_code != 200:
        return {"rules": [], "status": r.status_code}
    return {"rules": r.json().get("elements") or [], "status": 200}


async def create_conversion_rule(name: str, conv_type: str = "LEAD") -> dict:
    """Create a ``CONVERSIONS_API`` rule — ``POST /rest/conversions``.

    Returns ``{"rule_urn": "urn:lla:llaPartnerConversion:{id}", "id": ...}``
    on success (id from ``x-restli-id`` / body ``id``).
    """
    ctx = await _ctx()
    if not ctx:
        return {"error": "disabled-or-no-account"}
    token, _ = ctx
    payload = {
        "name": name,
        "account": AD_ACCOUNT_URN,
        "conversionMethod": "CONVERSIONS_API",
        "postClickAttributionWindowSize": 30,
        "viewThroughAttributionWindowSize": 7,
        "attributionType": "LAST_TOUCH_BY_CAMPAIGN",
        "type": conv_type,
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(
            f"{REST}/conversions",
            params={"autoAssociationType": "ALL_CAMPAIGNS"},
            json=payload,
        )
    if r.status_code not in (200, 201):
        return {"error": r.status_code, "body": r.text[:300]}
    rule_id = r.headers.get("x-restli-id")
    if not rule_id:
        try:
            rule_id = r.json().get("id")
        except ValueError:
            rule_id = None
    if not rule_id:
        return {"error": "no-rule-id", "body": r.text[:300]}
    return {"rule_urn": f"urn:lla:llaPartnerConversion:{rule_id}", "id": str(rule_id), "name": name}


async def resolve_conversion_rule(event: str) -> str | None:
    """Rule URN for *event*: env var first, then ``social_secrets`` fallback.

    The ensure-task stores auto-created rules under the same env-var key in
    the secret store, so resolution order stays env → secret → None.
    """
    spec = CONVERSION_EVENTS.get(event)
    if not spec:
        return None
    env_key, _ = spec
    urn = os.environ.get(env_key, "")
    if urn:
        return urn
    try:
        from app.services.secret_store import secret_store

        val = await secret_store.get(env_key)
        return str(val) if val else None
    except Exception:  # noqa: BLE001 — resolution must never break the caller
        logger.debug("conversion rule secret lookup failed for %s", env_key, exc_info=True)
        return None


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
        "destinations": [{"destination": "LINKEDIN"}],
    }
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.post(f"{REST}/dmpSegments", json=payload)
        if r.status_code in (200, 201):
            return {"urn": r.headers.get("x-restli-id") or r.json().get("id")}
        return {"error": r.status_code, "body": r.text[:300]}


async def upload_dmp_elements(segment_urn: str, elements: list[dict]) -> dict:
    """POST raw DMP segment elements — each already shaped
    ``{"action": "ADD", ...}`` (userIds and/or profile fields)."""
    ctx = await _ctx()
    if not ctx:
        return {"skip": "disabled-or-no-account"}
    token, _ = ctx
    payload = {"elements": elements}
    async with httpx.AsyncClient(
        timeout=60,
        headers={
            "Authorization": f"Bearer {token}",
            **HDRS,
            "X-RestLi-Method": "BATCH_CREATE",
        },
    ) as c:
        r = await c.post(f"{REST}/dmpSegments/{segment_urn.split(':')[-1]}/users", json=payload)
        return {"status": r.status_code, "ok": r.status_code in (200, 201, 204)}


async def upload_dmp_users(segment_urn: str, users: list[dict | list[dict]]) -> dict:
    """Attach users to a segment. ``users`` is a list of TypedId dicts
    (``{"idType": "SHA256_EMAIL", "idValue": ...}``) or profile-field dicts;
    a nested list becomes one element whose ``userIds`` carries the whole
    chunk — LinkedIn accepts up to 5000 ids per call."""
    elements = []
    for u in users:
        ids = u if isinstance(u, list) else [u]
        elements.append({"action": "ADD", "userIds": ids})
    return await upload_dmp_elements(segment_urn, elements)
