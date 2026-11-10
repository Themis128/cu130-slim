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

    talk_room = await _provision_talk_room(payload) if _secret() else None
    return JSONResponse({"ok": True, "event": event, "conversion": out, "talk_room": talk_room})


async def _provision_talk_room(payload: dict) -> str | None:
    """Create the per-booking Talk room immediately so the join link exists
    before the 30-min meeting-notes sweep would provision it.

    Gated on CALCOM_WEBHOOK_SECRET being set — unsigned payloads are only
    accepted in dev, and room creation is a write we don't want spoofable.
    Best-effort: never raises into the webhook response.
    """
    try:
        from app.services import meeting_notes
        from app.services import nextcloud_talk as talk

        inner = payload.get("payload") or {}
        attendees = inner.get("attendees") or [{}]
        attendee = attendees[0] or {}
        name = str(attendee.get("name") or "").strip()
        email = str(attendee.get("email") or "").strip().lower()
        start = str(inner.get("startTime") or inner.get("start") or "")[:10]
        if not talk.talk_enabled():
            return None
        room_name = f"Consultation — {name or email or 'guest'}" + (f" — {start}" if start else "")
        token = await talk.create_room(
            room_name[:180],
            description=(
                "Cloudless consultation call.\n"
                f"Booked via Cal.com — {email or 'guest'}.\n"
                "Recording enabled for internal meeting notes."
            ),
        )
        if not token:
            return None
        await talk.post_to_room(
            token,
            "👋 Consultation room — join with the video button. "
            "The call may be recorded for internal meeting notes.",
        )
        link = talk.room_url(token)
        # Marker on the Lead — the meeting-notes sweep resolves
        # opportunity → contact email → lead notes for the room token.
        from app.services import espocrm_client as espo

        lead_id = await espo.upsert_lead(
            {
                "emailAddress": email,
                "firstName": name.split(" ")[0] if name else "",
                "lastName": " ".join(name.split(" ")[1:]) if name else "",
                "source": "Web Site",
                "description": f"{meeting_notes.ROOM_TAG}{token} — Cal.com booking join: {link}",
            }
        )
        if lead_id:
            await espo.add_lead_note(
                lead_id,
                f"{meeting_notes.ROOM_TAG}{token} — join: {link}",
                entity="Lead",
            )
        return link or token
    except Exception:  # noqa: BLE001
        logger.exception("calcom→talk room provisioning failed")
        return None
