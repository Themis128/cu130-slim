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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/calcom", tags=["webhooks"])


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
async def calcom_booking(request: Request) -> Any:
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
    return JSONResponse({"ok": True, "event": event, "conversion": out})
