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
