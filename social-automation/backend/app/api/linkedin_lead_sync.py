"""LinkedIn Lead Sync webhook receiver — push notifications → CRM + conversion.

Campaign Manager Lead Sync POSTs a notification per new lead-form response:

    {"leadFormResponse": "urn:li:leadFormResponse:{id}", "owner": {...}, ...}

(or a list of such objects). The receiver fetches the full response via
``GET /rest/leadFormResponses/{id}`` and routes it through the same
``route_lead_response`` path as the polling task — EspoCRM upsert +
``lead_created`` Conversions API event.

Auth: ``X-Lead-Sync-Key`` must match ``LINKEDIN_LEAD_SYNC_KEY`` when that env
is set (Lead Sync has no request signature — the shared key is the gate).
Register the webhook URL in Campaign Manager → Lead Sync settings as
``https://<api-host>/api/v1/webhooks/linkedin-lead-sync``.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/linkedin-lead-sync", tags=["webhooks"])


def _authorized(request: Request) -> bool:
    key = os.environ.get("LINKEDIN_LEAD_SYNC_KEY", "")
    if not key:
        return True
    return request.headers.get("x-lead-sync-key", "") == key


def _response_urns(payload: Any) -> list[str]:
    items = payload if isinstance(payload, list) else [payload]
    urns: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        urn = it.get("leadFormResponse") or it.get("leadFormResponseUrn") or ""
        if isinstance(urn, str) and urn.startswith("urn:li:"):
            urns.append(urn)
        # Some payloads nest the URN one level deeper.
        nested = it.get("leadFormResponse")
        if isinstance(nested, dict):
            u = nested.get("urn") or nested.get("id") or ""
            if str(u).startswith("urn:li:"):
                urns.append(str(u))
    return urns


async def _fetch_response(urn: str) -> dict | None:
    """GET /rest/leadFormResponses/{id} — full response entity."""
    import httpx

    from app.services import linkedin_advanced

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return None
    token, _ = ctx
    rid = urn.rsplit(":", 1)[-1]
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **linkedin_advanced.HDRS}) as c:
        r = await c.get(f"{linkedin_advanced.REST}/leadFormResponses/{rid}")
    if r.status_code != 200:
        logger.warning("lead-sync fetch %s → %s", urn, r.status_code)
        return None
    return r.json()


@router.post("")
async def lead_sync_webhook(request: Request) -> Any:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "bad key"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — malformed body still gets a 200 so LI doesn't retry-storm
        logger.warning("lead-sync webhook: unparseable body")
        return JSONResponse({"ok": True, "skipped": "bad-json"})

    urns = _response_urns(payload)
    if not urns:
        # Challenge/verification ping or unknown shape — acknowledge.
        return JSONResponse({"ok": True, "skipped": "no-leadFormResponse", "keys": list(payload)[:8] if isinstance(payload, dict) else "list"})

    from app.services.slack_notifications import post_alert_to_slack
    from app.worker.tasks.linkedin_lead_boost import route_lead_response

    routed = 0
    errors: list[str] = []
    for urn in urns[:25]:
        resp = await _fetch_response(urn)
        if resp is None:
            # Fall back to the poller for this batch rather than losing the lead.
            try:
                from app.worker.tasks.linkedin_lead_boost import route_linkedin_leads

                route_linkedin_leads.delay()
            except Exception as exc:  # noqa: BLE001
                logger.warning("lead-sync fallback dispatch failed: %s", exc)
            errors.append(f"{urn}: fetch failed — poller queued")
            continue
        try:
            if await route_lead_response(resp):
                routed += 1
        except Exception as exc:  # noqa: BLE001 — one bad lead must not sink the batch
            logger.warning("lead-sync route %s failed: %s", urn, exc)
            errors.append(f"{urn}: {exc}")

    if routed:
        await post_alert_to_slack(f":new: LinkedIn Lead Sync webhook: {routed} lead(s) routed")
    logger.info("lead-sync webhook: %d urns, %d routed, %d errors", len(urns), routed, len(errors))
    return JSONResponse({"ok": True, "received": len(urns), "routed": routed, "errors": errors})
