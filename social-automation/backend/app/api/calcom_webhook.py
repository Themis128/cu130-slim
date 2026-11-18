"""Cal.com booking webhook → LinkedIn Conversions API (attribution loop).

Cal.com fires ``booking.created`` on every consultation booking — we
stream ``audit_booked`` (+ ``lead_created``) to the Conversions API so
post→click→booking attribution lands server-side. Behind
``LINKEDIN_ADVANCED=1`` (no-ops as accepted until grants land).

Set the webhook secret in Cal.com → Webhooks; it signs the payload with
``X-Cal-Signature-256``.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.web_analytics import WebAnalyticsConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/calcom", tags=["webhooks"])

# Booking metadata keys the site attaches at booking time (first-touch
# attribution) — mapped onto the utm_* payload fields the reports group by.
_ATTRIBUTION_KEYS = {
    "utm_source": "utm_source",
    "utm_medium": "utm_medium",
    "utm_campaign": "utm_campaign",
    "utm_term": "utm_term",
    "utm_content": "utm_content",
    "referrer": "referrer",
    "landing_page": "landing_page",
    "landingPage": "landing_page",
}


async def _record_booking_attribution(db: AsyncSession, payload: dict) -> None:
    """Persist a ``booking_created`` web-analytics event carrying the
    first-touch attribution the site attached as Cal.com booking metadata.

    This is what lets the attribution digest count bookings per channel —
    Cal.com fires the webhook server-side, so no browser-side tag is needed.
    Best-effort: never raises into the webhook response.
    """
    try:
        inner = payload.get("payload") or {}
        meta = inner.get("metadata") or {}
        if not isinstance(meta, dict):
            return
        # The site may nest attribution under a single key or spread it flat.
        flat: dict[str, Any] = {}
        for k, v in meta.items():
            if isinstance(v, str) and v.strip():
                flat[str(k)] = v.strip()[:300]
        nested = flat.get("attribution")
        if isinstance(nested, str):
            try:
                import json

                nested = json.loads(nested)
            except Exception:  # noqa: BLE001
                nested = None
        source_meta = nested if isinstance(nested, dict) else flat

        attrs = {out_k: source_meta[in_k] for in_k, out_k in _ATTRIBUTION_KEYS.items() if source_meta.get(in_k)}
        # camelCase site fields (utmSource etc.) as fallback
        for ck, uk in (
            ("utmSource", "utm_source"),
            ("utmMedium", "utm_medium"),
            ("utmCampaign", "utm_campaign"),
            ("utmTerm", "utm_term"),
            ("utmContent", "utm_content"),
        ):
            if not attrs.get(uk) and source_meta.get(ck):
                attrs[uk] = str(source_meta[ck])[:300]

        config = (await db.execute(select(WebAnalyticsConfig).where(WebAnalyticsConfig.domain == "cloudless.gr").limit(1))).scalar_one_or_none()
        if config is None:
            return

        from app.services.web_analytics import ingest_event

        attendee = (inner.get("attendees") or [{}])[0] or {}
        event = await ingest_event(
            team_id=config.team_id,
            config=config,
            domain="cloudless.gr",
            event_name="booking_created",
            payload={
                **attrs,
                "booking_uid": inner.get("uid"),
                "start": inner.get("startTime") or inner.get("start"),
                "attendee_email_sha": hashlib.sha256(str(attendee.get("email") or "").strip().lower().encode()).hexdigest()[:16]
                if attendee.get("email")
                else None,
            },
            path="/consultation",
            referrer=attrs.get("referrer"),
        )
        db.add(event)
        await db.commit()
        logger.info("calcom booking attribution recorded: %s", attrs or "(no utm)")
    except Exception:  # noqa: BLE001
        logger.exception("calcom booking attribution write failed")


def _secret() -> bytes:
    return os.environ.get("CALCOM_WEBHOOK_SECRET", "").encode()


def _verify_sig(body: bytes, sig: str | None) -> bool:
    sec = _secret()
    if not sec:  # no secret configured — accept (dev/local)
        return True
    if not sig:
        return False
    expect = hmac.new(sec, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)


def _event_name(payload: dict) -> str | None:
    t = (payload.get("triggerEvent") or payload.get("type") or "").lower()
    if "booking" in t and ("created" in t or "confirmed" in t):
        return "audit_booked"
    if "booking" in t:
        return "lead_created"
    return None


@router.post("")
async def calcom_booking(request: Request, db: AsyncSession = Depends(get_db)) -> Any:
    body = await request.body()
    if not _verify_sig(body, request.headers.get("x-cal-signature-256")):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = await request.json()
    event = _event_name(payload)
    if not event:
        return JSONResponse({"ok": True, "skipped": "not-a-booking-event"})

    attendee = (payload.get("payload") or {}).get("attendees") or [{}]
    email = (attendee[0] or {}).get("email") or ""
    sha = hashlib.sha256(email.strip().lower().encode()).hexdigest() if email else None

    from app.services import linkedin_advanced

    out = await linkedin_advanced.stream_conversion(
        conversion_rule=os.environ.get("LINKEDIN_CONVERSION_RULE", "audit_booked"),
        happened_at_ms=int(time.time() * 1000),
        email_sha256=sha,
    )
    logger.info("calcom→linkedin conversion %s for %s → %s", event, email[:3] + "***" if email else "no-email", out)

    if "created" in (payload.get("triggerEvent") or "").lower():
        await _record_booking_attribution(db, payload)

    talk_room = None
    trigger = (payload.get("triggerEvent") or "").lower()
    if _secret() and ("created" in trigger or "confirmed" in trigger):
        talk_room = await _provision_talk_room(payload)
    return JSONResponse({"ok": True, "event": event, "conversion": out, "talk_room": talk_room})


async def _provision_talk_room(payload: dict) -> str | None:
    """Provision the per-booking Talk room via the shared idempotent path —
    the site may already have created it (booking-time endpoint), in which
    case the Lead marker dedupes and we return the existing link.

    Gated on CALCOM_WEBHOOK_SECRET being set — unsigned payloads are only
    accepted in dev, and room creation is a write we don't want spoofable.
    Best-effort: never raises into the webhook response.
    """
    try:
        from app.services import meeting_notes

        inner = payload.get("payload") or {}
        attendees = inner.get("attendees") or [{}]
        attendee = attendees[0] or {}
        name = str(attendee.get("name") or "").strip()
        email = str(attendee.get("email") or "").strip().lower()
        start = str(inner.get("startTime") or inner.get("start") or "")
        out = await meeting_notes.provision_consult_room(name=name, email=email, start=start)
        return (out or {}).get("url") or (out or {}).get("token")
    except Exception:  # noqa: BLE001
        logger.exception("calcom→talk room provisioning failed")
        return None
