"""LinkedIn Lead Sync webhook — challenge validation + signed notifications.

Implements the official webhook contract (Microsoft Learn, verified
2026-11-15):

- ``GET ?challengeCode=…`` → 200 ``{"challengeCode", "challengeResponse"}``
  where response = lowercase hex HMAC-SHA256(challengeCode, clientSecret).
  Re-validated ~every 2h; 3 failures → endpoint blocked.
- ``POST`` → verify ``X-LI-Signature`` = hex(HMAC-SHA256("hmacsha256=" +
  raw body, clientSecret)), constant-time compare, then route
  ``leadGenFormResponse`` URNs through ``route_lead_response`` (EspoCRM +
  ``lead_created`` conversion). ``leadAction=DELETED`` is acked, not routed.
- Dedup key = ``leadGenFormResponse + occurredAt`` (the response URN is
  REUSED across resubmissions — occurredAt is what differentiates them).

Subscription itself is created via ``POST /rest/leadNotifications``
(``r_marketing_leadgen_automation`` — already granted) — see
``linkedin_advanced.create_lead_notification`` / the
``linkedin_feed_tool.py register-lead-sync`` command.
"""

import hashlib
import hmac
import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/linkedin-lead-sync", tags=["webhooks"])


def _client_secret() -> str:
    return os.environ.get("LINKEDIN_CLIENT_SECRET", "")


@router.get("")
async def lead_sync_challenge(request: Request) -> Any:
    """LinkedIn webhook ownership challenge — must answer <3s."""
    code = request.query_params.get("challengeCode", "")
    secret = _client_secret()
    if not code or not secret:
        return JSONResponse({"error": "missing challengeCode or client secret"}, status_code=400)
    digest = hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()
    return JSONResponse({"challengeCode": code, "challengeResponse": digest})


def _signature_ok(request: Request, raw_body: bytes) -> bool:
    sig = request.headers.get("x-li-signature", "")
    secret = _client_secret()
    if not sig or not secret:
        # No secret configured → can't verify; accept but log (dev parity with
        # other webhook lanes). With a secret set, bad/missing sig = reject.
        return not secret
    expected = hmac.new(secret.encode(), b"hmacsha256=" + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _response_ids(payload: Any) -> list[dict]:
    """Extract (response_id, dedup_key, action) tuples from notification(s)."""
    items = payload if isinstance(payload, list) else [payload]
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("type") not in (None, "LEAD_ACTION"):
            continue
        urn = it.get("leadGenFormResponse") or ""
        if not isinstance(urn, str) or ("leadGenFormResponse:" not in urn and "adFormResponse:" not in urn):
            continue
        rid = urn.rsplit(":", 1)[-1]
        occurred = it.get("occurredAt") or it.get("createdAt") or ""
        owner = it.get("owner") or {}
        out.append(
            {
                "id": rid,
                "urn": urn,
                "key": f"{urn}:{occurred}",
                "action": it.get("leadAction") or "CREATED",
                "lead_type": it.get("leadType"),
                "form": it.get("leadGenForm"),
                "owner": owner.get("sponsoredAccount") or owner.get("organization") or "",
            }
        )
    return out


async def _already_seen(db, key: str) -> bool:
    from sqlalchemy import select

    from app.models.analytics import AnalyticsEvent

    row = (
        await db.execute(
            select(AnalyticsEvent.id)
            .where(
                AnalyticsEvent.event_type == "linkedin_lead_sync",
                AnalyticsEvent.platform_event_id == key,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def _mark_seen(db, key: str, meta: dict) -> None:
    from datetime import UTC, datetime

    from app.models.analytics import AnalyticsEvent

    db.add(
        AnalyticsEvent(
            event_type="linkedin_lead_sync",
            platform="linkedin",
            platform_event_id=key,
            occurred_at=datetime.now(UTC),
            meta_data=meta,
        )
    )
    await db.commit()


async def _fetch_response(rid: str, owner_urn: str, lead_type: str) -> dict | None:
    """Batch-get one lead response — ``ids=List("…")`` + owner + leadType.

    Per the Lead Sync docs there is no ``/leadFormResponses/{id}`` path;
    responses come back under ``results[id]``.
    """
    import httpx

    from app.services import linkedin_advanced

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return None
    token, _ = ctx
    owner_key = "sponsoredAccount" if "sponsoredAccount" in owner_urn else "organization"
    # Rest.li params are pre-encoded in the URL — httpx params= double-encodes.
    enc = quote(owner_urn, safe="")
    lt = quote(lead_type or "SPONSORED", safe="")
    url = f'{linkedin_advanced.REST}/leadFormResponses?ids=List("{rid}")&owner=({owner_key}:{enc})&leadType=(leadType:{lt})'
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **linkedin_advanced.HDRS}) as c:
        r = await c.get(url)
    if r.status_code != 200:
        logger.warning("lead-sync fetch %s → %s", rid, r.status_code)
        return None
    data = r.json()
    results = data.get("results") or {}
    return results.get(rid) or next(iter(results.values()), None)


@router.post("")
async def lead_sync_webhook(request: Request) -> Any:
    raw = await request.body()
    if not _signature_ok(request, raw):
        logger.warning("lead-sync webhook: bad X-LI-Signature")
        return JSONResponse({"ok": False, "error": "bad signature"}, status_code=401)

    import json

    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.warning("lead-sync webhook: unparseable body")
        return JSONResponse({"ok": True, "skipped": "bad-json"})

    items = _response_ids(payload)
    if not items:
        return JSONResponse({"ok": True, "skipped": "no-leadFormResponse"})

    from app.services.slack_notifications import post_alert_to_slack
    from app.worker._async import task_session
    from app.worker.tasks.linkedin_lead_boost import route_lead_response

    routed = skipped = 0
    errors: list[str] = []
    async with task_session() as db:
        for it in items[:25]:
            if await _already_seen(db, it["key"]):
                skipped += 1
                continue
            await _mark_seen(db, it["key"], {"urn": it["urn"], "action": it["action"], "lead_type": it["lead_type"]})
            if it["action"] == "DELETED":
                continue
            resp = await _fetch_response(it["id"], str(it.get("owner") or ""), str(it.get("lead_type") or ""))
            if resp is None:
                errors.append(f"{it['urn']}: fetch failed")
                continue
            try:
                if await route_lead_response(resp, str(it.get("form") or "")):
                    routed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("lead-sync route %s failed: %s", it["urn"], exc)
                errors.append(f"{it['urn']}: route failed")

    if routed:
        await post_alert_to_slack(f":new: LinkedIn Lead Sync: {routed} lead(s) routed")
    logger.info("lead-sync webhook: %d items, %d routed, %d deduped, %d errors", len(items), routed, skipped, len(errors))
    return JSONResponse({"ok": True, "received": len(items), "routed": routed, "deduped": skipped, "errors": errors})
