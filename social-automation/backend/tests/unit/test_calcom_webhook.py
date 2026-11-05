"""Coverage for the Cal.com → Conversions webhook."""

import hashlib
import hmac

import pytest

import app.api.calcom_webhook as W


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
