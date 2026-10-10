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


# ── Media / poll / keyboard additions ────────────────────────────────


@pytest.mark.asyncio
async def test_send_photo_payload(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"message_id": 5}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_photo(
            42, "https://x.test/p.png", caption="cap",
            reply_markup={"inline_keyboard": [[{"text": "Go", "url": "https://x.test"}]]},
        )
    body = fake.calls[0]["json"]
    assert fake.calls[0]["url"].endswith("/sendPhoto")
    assert body["photo"] == "https://x.test/p.png"
    assert body["caption"] == "cap"
    assert body["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://x.test"


@pytest.mark.asyncio
async def test_send_media_group_validation(client):
    with pytest.raises(ValueError, match="2-10"):
        await client.send_media_group(42, [{"type": "photo", "media": "https://x/1.png"}])
    with pytest.raises(ValueError, match="photo or video"):
        await client.send_media_group(
            42,
            [
                {"type": "photo", "media": "https://x/1.png"},
                {"type": "audio", "media": "https://x/2.mp3"},
            ],
        )
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": [{}, {}]}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        result = await client.send_media_group(
            42,
            [
                {"type": "photo", "media": "https://x/1.png"},
                {"type": "photo", "media": "https://x/2.png", "caption": "c"},
            ],
        )
    assert len(result) == 2
    assert fake.calls[0]["json"]["media"][1]["caption"] == "c"


@pytest.mark.asyncio
async def test_send_poll_payload(client):
    with pytest.raises(ValueError, match="2-10"):
        await client.send_poll(42, "q?", ["only one"])
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"poll": {"id": "p1"}}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_poll(42, "Pick one", ["a", "b"], allows_multiple_answers=True)
    body = fake.calls[0]["json"]
    assert body["options"] == [{"text": "a"}, {"text": "b"}]
    assert body["allows_multiple_answers"] is True


@pytest.mark.asyncio
async def test_send_message_with_markup(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"message_id": 9}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_message_with_markup(
            42, "click", {"inline_keyboard": [[{"text": "B", "callback_data": "d"}]]}
        )
    body = fake.calls[0]["json"]
    assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "d"


@pytest.mark.asyncio
async def test_answer_callback_and_pin_delete(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": True}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        assert await client.answer_callback_query("cb1", text="thanks") is True
        assert await client.pin_chat_message(42, 10) is True
        assert await client.delete_message(42, 10) is True
        assert await client.send_chat_action(42, "typing") is True
    assert fake.calls[0]["json"]["text"] == "thanks"
    assert fake.calls[3]["json"]["action"] == "typing"


@pytest.mark.asyncio
async def test_send_chat_action_rejects_unknown(client):
    with pytest.raises(ValueError, match="unknown chat action"):
        await client.send_chat_action(42, "exploding")


def test_inline_keyboard_builder():
    kb = api.inline_keyboard([[{"text": "Audit", "url": "https://c.test"}]])
    assert kb["inline_keyboard"][0][0]["url"] == "https://c.test"
    with pytest.raises(ValueError, match="needs url"):
        api.inline_keyboard([[{"text": "no action"}]])
    with pytest.raises(ValueError, match="text is required"):
        api.inline_keyboard([[{"url": "https://c.test"}]])


def test_extract_callback_query():
    update = {
        "update_id": 3,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 7, "username": "ada"},
            "data": "cta:audit",
            "message": {"message_id": 20, "chat": {"id": 99}},
        },
    }
    cb = api.extract_callback_query(update)
    assert cb is not None
    assert cb["callback_id"] == "cb-1"
    assert cb["data"] == "cta:audit"
    assert cb["chat_id"] == 99
    assert api.extract_callback_query({"update_id": 4}) is None
