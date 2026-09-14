"""Unit tests for Telegram Bot API client + webhook helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import telegram_api as api


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

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

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        return self.response


@pytest.fixture
def client():
    return api.TelegramAPIClient(bot_token="123456:ABC-DEF")


def test_client_rejects_bad_token():
    with pytest.raises(ValueError, match="Invalid Telegram bot token"):
        api.TelegramAPIClient(bot_token="not-a-token")


@pytest.mark.asyncio
async def test_get_me_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "cloudless_bot"}})
    )
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        me = await client.get_me()
    assert me["username"] == "cloudless_bot"
    assert fake.calls[0]["url"].endswith("/getMe")


@pytest.mark.asyncio
async def test_get_me_api_error(client):
    fake = _FakeAsyncClient(
        _FakeResponse(401, {"ok": False, "description": "Unauthorized", "error_code": 401})
    )
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.TelegramAPIError) as exc:
            await client.get_me()
    assert exc.value.detail == "Unauthorized"


@pytest.mark.asyncio
async def test_set_webhook_requires_https(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.set_webhook("http://example.com/hook")


@pytest.mark.asyncio
async def test_set_webhook_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": True}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        ok = await client.set_webhook(
            "https://social.cloudless.gr/api/v1/telegram/webhook/abc",
            secret_token="sec_token_1",
            allowed_updates=["message"],
        )
    assert ok is True
    payload = fake.calls[0]["json"]
    assert payload["url"].startswith("https://")
    assert payload["secret_token"] == "sec_token_1"
    assert payload["allowed_updates"] == ["message"]


@pytest.mark.asyncio
async def test_send_message_truncates(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"ok": True, "result": {"message_id": 9, "text": "x"}})
    )
    long_text = "a" * 5000
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_message(42, long_text)
    assert len(fake.calls[0]["json"]["text"]) == api.MAX_MESSAGE_CHARS
    assert fake.calls[0]["json"]["chat_id"] == 42


def test_extract_inbound_text_update():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "Hello",
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "first_name": "Ada", "username": "ada", "is_bot": False},
        },
    }
    inbound = api.extract_inbound_text_update(update)
    assert inbound is not None
    assert inbound["chat_id"] == 99
    assert inbound["text"] == "Hello"
    assert inbound["sender_name"] == "Ada"


def test_extract_inbound_ignores_sticker():
    update = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "sticker": {"emoji": "😀"},
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "is_bot": False},
        },
    }
    assert api.extract_inbound_text_update(update) is None


def test_telegram_api_error_detail_alias():
    exc = api.TelegramAPIError(400, "Bad Request", method="sendMessage")
    assert exc.detail == "Bad Request"
