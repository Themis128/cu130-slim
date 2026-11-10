"""Coverage for the Cal.com → Conversions webhook."""

import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest

import app.api.calcom_webhook as W
import app.services.espocrm_client as ESPO
import app.services.meeting_notes as MN
import app.services.nextcloud_talk as TALK


def test_event_name():
    assert W._event_name({"triggerEvent": "BOOKING_CREATED"}) == "audit_booked"
    assert W._event_name({"triggerEvent": "booking.cancelled"}) == "lead_created"
    assert W._event_name({"triggerEvent": "other"}) is None


def test_sig_verify(monkeypatch):
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "s")
    body = b'{"x":1}'
    sig = hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert W._verify_sig(body, sig) is True
    assert W._verify_sig(body, "bad") is False


@pytest.mark.asyncio
async def test_booking_posts_conversion(monkeypatch):
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "")
    calls = []

    class _Adv:
        async def stream_conversion(self, **kw):
            calls.append(kw)
            return {"ok": True}

    import app.services.linkedin_advanced as A

    monkeypatch.setattr(A, "stream_conversion", _Adv().stream_conversion)

    class Req:
        headers = {}

        async def body(self):
            return b'{"triggerEvent":"BOOKING_CREATED","payload":{"attendees":[{"email":"t@x.com"}]}}'

        async def json(self):
            return {"triggerEvent": "BOOKING_CREATED", "payload": {"attendees": [{"email": "t@x.com"}]}}

    await W.calcom_booking(Req())
    assert calls and calls[0]["email_sha256"]
    assert calls[0]["conversion_rule"] == "audit_booked"


# ── _provision_talk_room ──────────────────────────────────────────────


def _payload(**over):
    p = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "attendees": [{"name": "Jane Doe", "email": "jane@acme.com"}],
            "startTime": "2026-11-15T10:00:00Z",
        },
    }
    p["payload"].update(over)
    return p


@pytest.mark.asyncio
async def test_provision_talk_disabled(monkeypatch):
    import app.services.nextcloud_talk as TALK

    monkeypatch.setattr(TALK, "talk_enabled", lambda: False)
    assert await W._provision_talk_room(_payload()) is None


@pytest.mark.asyncio
async def test_provision_talk_room_happy(monkeypatch):

    monkeypatch.setattr(TALK, "talk_enabled", lambda: True)
    monkeypatch.setattr(TALK, "create_room", AsyncMock(return_value="abc12345"))
    monkeypatch.setattr(TALK, "post_to_room", AsyncMock(return_value=True))
    monkeypatch.setattr(TALK, "room_url", lambda t: f"https://x/call/{t}")
    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value="lead-1"))
    monkeypatch.setattr(ESPO, "add_lead_note", AsyncMock(return_value=True))

    out = await W._provision_talk_room(_payload())
    assert out == "https://x/call/abc12345"
    assert "Consultation — Jane Doe" in TALK.create_room.await_args.args[0]
    ESPO.add_lead_note.assert_awaited()
    assert MN.ROOM_TAG in ESPO.add_lead_note.await_args.args[1]


@pytest.mark.asyncio
async def test_provision_talk_room_failures(monkeypatch):

    monkeypatch.setattr(TALK, "talk_enabled", lambda: True)
    # create_room fails → None
    monkeypatch.setattr(TALK, "create_room", AsyncMock(return_value=None))
    assert await W._provision_talk_room(_payload()) is None
    # espo raises → swallowed → None
    monkeypatch.setattr(TALK, "create_room", AsyncMock(return_value="abc12345"))
    monkeypatch.setattr(TALK, "post_to_room", AsyncMock(return_value=True))
    monkeypatch.setattr(TALK, "room_url", lambda t: "u")
    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(side_effect=RuntimeError("x")))
    assert await W._provision_talk_room(_payload()) is None


@pytest.mark.asyncio
async def test_webhook_bad_sig_and_non_booking(monkeypatch):
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "sec")

    class Req:
        headers = {"x-cal-signature-256": "bad"}

        async def body(self):
            return b"{}"

        async def json(self):
            return {}

    import fastapi

    with pytest.raises(fastapi.HTTPException) as ei:
        await W.calcom_booking(Req())
    assert ei.value.status_code == 401

    # missing signature also 401s when secret set
    class Req2(Req):
        headers = {}

    with pytest.raises(fastapi.HTTPException):
        await W.calcom_booking(Req2())

    # valid sig + non-booking event → skipped
    monkeypatch.setattr(
        "app.services.linkedin_advanced.stream_conversion",
        AsyncMock(return_value={"ok": True}),
    )

    class Req3:
        headers = {}

        def __init__(self):
            body = b'{"triggerEvent":"PING"}'
            self.headers = {"x-cal-signature-256": hmac.new(b"sec", body, hashlib.sha256).hexdigest()}

        async def body(self):
            return b'{"triggerEvent":"PING"}'

        async def json(self):
            return {"triggerEvent": "PING"}

    res = await W.calcom_booking(Req3())
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_webhook_booking_provisions_talk_room(monkeypatch):
    """signed BOOKING_CREATED → _provision_talk_room runs, room in response."""
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "sec")
    monkeypatch.setattr(
        "app.services.linkedin_advanced.stream_conversion",
        AsyncMock(return_value={"ok": True}),
    )
    prov = AsyncMock(return_value="https://x/call/abc12345")
    monkeypatch.setattr(W, "_provision_talk_room", prov)

    body = b'{"triggerEvent":"BOOKING_CREATED","payload":{"attendees":[{"email":"t@x.com"}]}}'

    class Req:
        def __init__(self):
            self.headers = {"x-cal-signature-256": hmac.new(b"sec", body, hashlib.sha256).hexdigest()}

        async def body(self):
            return body

        async def json(self):
            return {"triggerEvent": "BOOKING_CREATED", "payload": {"attendees": [{"email": "t@x.com"}]}}

    res = await W.calcom_booking(Req())
    assert res.status_code == 200
    prov.assert_awaited_once()
