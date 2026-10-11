"""Unit tests for Viber REST API client + webhook helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from app.services import viber_api as api


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(payload or {})

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


@pytest.fixture
def client():
    return api.ViberAPIClient(auth_token="abc123def-456")


def test_client_rejects_bad_token():
    with pytest.raises(ValueError, match="Invalid Viber auth token"):
        api.ViberAPIClient(auth_token="bad token with spaces!!!")


@pytest.mark.asyncio
async def test_get_account_info_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"status": 0, "status_message": "ok", "id": "pa:123", "name": "Cloudless"})
    )
    with patch("app.services.viber_api.httpx.AsyncClient", return_value=fake):
        data = await client.get_account_info()
    assert data["name"] == "Cloudless"
    call = fake.calls[0]
    assert call["url"] == "https://chatapi.viber.com/pa/get_account_info"
    assert call["headers"]["X-Viber-Auth-Token"] == "abc123def-456"


@pytest.mark.asyncio
async def test_nonzero_status_raises(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"status": 6, "status_message": "notSubscribed"})
    )
    with patch("app.services.viber_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.ViberAPIError) as excinfo:
            await client.send_text("user1", "hello")
    assert excinfo.value.viber_status == 6


@pytest.mark.asyncio
async def test_send_text_payload(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": 0, "message_token": 555}))
    with patch("app.services.viber_api.httpx.AsyncClient", return_value=fake):
        result = await client.send_text("u1", "hi there", sender_name="Cloudless")
    body = fake.calls[0]["json"]
    assert body["receiver"] == "u1"
    assert body["type"] == "text"
    assert body["text"] == "hi there"
    assert body["sender"]["name"] == "Cloudless"
    assert body["min_api_version"] == 7
    assert result["message_token"] == 555


@pytest.mark.asyncio
async def test_send_text_truncates_and_requires_text(client):
    with pytest.raises(ValueError):
        await client.send_text("u1", "   ")
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": 0}))
    with patch("app.services.viber_api.httpx.AsyncClient", return_value=fake):
        await client.send_text("u1", "x" * 7100)
    assert len(fake.calls[0]["json"]["text"]) == api.MAX_MESSAGE_CHARS


@pytest.mark.asyncio
async def test_send_picture_requires_https(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.send_picture("u1", "http://insecure/img.png")


@pytest.mark.asyncio
async def test_broadcast_list_cap(client):
    with pytest.raises(ValueError, match="300"):
        await client.broadcast_text([f"u{i}" for i in range(301)], "hi")


@pytest.mark.asyncio
async def test_set_webhook_requires_https(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.set_webhook("http://insecure/hook")


def test_verify_signature_roundtrip():
    token = "tok-abc-123"
    body = b'{"event":"message"}'
    sig = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    assert api.verify_signature(body, sig, token) is True
    assert api.verify_signature(body, sig, token + "x") is False
    assert api.verify_signature(body, "", token) is False
    assert api.verify_signature(body, sig, "") is False


def test_parse_message_event():
    payload = {
        "event": "message",
        "timestamp": 1,
        "message_token": 42,
        "sender": {"id": "u1", "name": "Themis", "country": "GR", "language": "el"},
        "message": {"type": "text", "text": "hello", "tracking_data": "t"},
    }
    ev = api.parse_webhook_event(payload)
    assert ev["event"] == "message"
    assert ev["user_id"] == "u1"
    assert ev["text"] == "hello"
    assert ev["message_token"] == 42


def test_parse_conversation_started_and_subscribed():
    ev = api.parse_webhook_event(
        {
            "event": "conversation_started",
            "user": {"id": "u9", "name": "A", "country": "GR", "language": "el"},
            "subscribed": False,
            "context": "welcome",
            "message_token": 7,
        }
    )
    assert ev["user_id"] == "u9"
    assert ev["context"] == "welcome"
    assert ev["subscribed"] is False

    ev = api.parse_webhook_event({"event": "subscribed", "user": {"id": "u9"}})
    assert ev["user_id"] == "u9"


def test_parse_status_events():
    ev = api.parse_webhook_event({"event": "delivered", "user_id": "u1", "message_token": 5})
    assert ev["user_id"] == "u1"
    assert ev["message_token"] == 5
    ev = api.parse_webhook_event({"event": "webhook"})
    assert ev["event"] == "webhook"


def _wire(response):
    fake = _FakeAsyncClient(response)
    return patch.object(api.httpx, "AsyncClient", return_value=fake), fake


# ── _call error branches ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_rejects_bad_method_name(client):
    with pytest.raises(api.ViberAPIError):
        await client._call("BAD-Method")


@pytest.mark.asyncio
async def test_call_invalid_json_raises(client):
    resp = _FakeResponse(500, payload=None)
    resp.json = lambda: (_ for _ in ()).throw(ValueError("bad json"))
    resp.text = "oops\nbroken"
    w, _ = _wire(resp)
    with w:
        with pytest.raises(api.ViberAPIError):
            await client._call("get_account_info")


@pytest.mark.asyncio
async def test_call_nondict_json_raises(client):
    resp = _FakeResponse(200, payload=None)
    resp.json = lambda: ["not", "a", "dict"]
    w, _ = _wire(resp)
    with w:
        with pytest.raises(api.ViberAPIError):
            await client._call("get_account_info")


# ── account / webhook methods ───────────────────────────────────────


@pytest.mark.asyncio
async def test_unset_webhook(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.unset_webhook()
    assert fake.calls[0]["json"]["url"] == ""


@pytest.mark.asyncio
async def test_set_webhook_with_event_types(client):
    w, fake = _wire(_FakeResponse(200, {
        "status": 0, "event_types": ["delivered", "seen"]}))
    with w:
        events = await client.set_webhook(
            "https://example.com/hook", event_types=["delivered", "seen"])
    assert events == ["delivered", "seen"]
    assert fake.calls[0]["json"]["event_types"] == ["delivered", "seen"]


@pytest.mark.asyncio
async def test_set_webhook_nondict_events(client):
    w, _ = _wire(_FakeResponse(200, {"status": 0, "event_types": "x"}))
    with w:
        events = await client.set_webhook("https://example.com/hook")
    assert events == []


# ── send_* methods ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_requires_receiver(client):
    with pytest.raises(ValueError, match="receiver"):
        await client.send_message("", {"type": "text", "text": "hi"})


@pytest.mark.asyncio
async def test_send_message_tracking_and_avatar(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.send_message(
            "u1", {"type": "text", "text": "hi"},
            sender_name="B", sender_avatar="https://a/x.png",
            tracking_data="t" * 5000)
    payload = fake.calls[0]["json"]
    assert payload["receiver"] == "u1"
    assert payload["sender"]["avatar"] == "https://a/x.png"
    assert len(payload["tracking_data"]) == 4096


@pytest.mark.asyncio
async def test_send_video_and_file_and_url(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.send_video("u1", "https://a/v.mp4", size=123,
                                duration=9, thumbnail="https://a/t.jpg")
        await client.send_file("u1", "https://a/f.pdf", size=5,
                               file_name="f.pdf")
        await client.send_url("u1", "https://example.com")
    video, file_, url_ = (c["json"] for c in fake.calls)
    assert video["type"] == "video" and video["duration"] == 9
    assert video["thumbnail"] == "https://a/t.jpg"
    assert file_["type"] == "file" and file_["file_name"] == "f.pdf"
    assert url_["type"] == "url" and url_["media"] == "https://example.com"


@pytest.mark.asyncio
async def test_send_video_rejects_http(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.send_video("u1", "http://a/v.mp4", size=1)


@pytest.mark.asyncio
async def test_broadcast_text_payload(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.broadcast_text(["u1", "u2"], "hello all")
    payload = fake.calls[0]["json"]
    assert payload["broadcast_list"] == ["u1", "u2"]
    assert payload["type"] == "text" and payload["text"] == "hello all"


@pytest.mark.asyncio
async def test_broadcast_requires_list_and_text(client):
    with pytest.raises(ValueError):
        await client.broadcast_message([], {"type": "text"})
    with pytest.raises(ValueError):
        await client.broadcast_text(["u1"], "   ")


# ── users ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_online_and_user_details(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.get_online(["u1"])
        await client.get_user_details("u1")
    assert fake.calls[0]["json"]["ids"] == ["u1"]
    assert fake.calls[1]["json"]["id"] == "u1"


@pytest.mark.asyncio
async def test_user_helpers_validate(client):
    with pytest.raises(ValueError):
        await client.get_online([])
    with pytest.raises(ValueError):
        await client.get_online(["u"] * 101)
    with pytest.raises(ValueError):
        await client.get_user_details("")


# ── helpers ─────────────────────────────────────────────────────────


def test_sanitize_log_text_strips_controls():
    out = api._sanitize_log_text("a\nb\rc\x00d" * 200)
    assert "\n" not in out and "\r" not in out and "\x00" not in out
    assert len(out) <= 400


def test_viber_api_error_attrs():
    err = api.ViberAPIError(400, "bad", viber_status=3, method="send_message")
    assert err.status_code == 400
    assert err.viber_status == 3
    assert err.method == "send_message"


def test_verify_signature_missing_args():
    assert api.verify_signature(b"body", "", "tok") is False
    assert api.verify_signature(b"body", "sig", "") is False


def test_parse_webhook_ping_and_unknown():
    ping = api.parse_webhook_event({"event": "webhook"})
    assert ping["event"] == "webhook" and "user_id" not in ping
    unknown = api.parse_webhook_event({"event": "something_else"})
    assert unknown["event"] == "something_else"
    empty = api.parse_webhook_event({})
    assert empty["event"] == ""


def test_parse_unsubscribed():
    out = api.parse_webhook_event({
        "event": "unsubscribed", "user": {"id": "u9", "name": "N"}})
    assert out["user_id"] == "u9" and out["user_name"] == "N"


@pytest.mark.asyncio
async def test_send_picture_optional_fields(client):
    w, fake = _wire(_FakeResponse(200, {"status": 0}))
    with w:
        await client.send_picture(
            "u1", "https://a/p.jpg", text="cap" * 2000,
            thumbnail="https://a/t.jpg")
        await client.send_picture("u1", "https://a/p2.jpg",
                                  thumbnail="http://bad")
    pic = fake.calls[0]["json"]
    assert pic["text"] and pic["thumbnail"] == "https://a/t.jpg"
    pic2 = fake.calls[1]["json"]
    assert "text" not in pic2 and "thumbnail" not in pic2


@pytest.mark.asyncio
async def test_send_file_rejects_http(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.send_file("u1", "http://a/f.pdf", size=1,
                               file_name="f.pdf")


def test_error_detail_property():
    with_description = api.ViberAPIError(400, "bad")
    assert with_description.detail == "bad"
    without = api.ViberAPIError(400, "")
    assert without.detail == str(without)
