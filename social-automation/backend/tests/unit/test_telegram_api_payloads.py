"""Payload-shape tests for TelegramAPIClient: optional parameters must be
omitted from the Bot API request when not supplied (Telegram rejects some
explicit nulls, e.g. ``reply_markup: null``), and included when they are.

``_call`` is the single HTTP seam, so it is replaced with a recorder and no
request leaves the process.
"""
from __future__ import annotations

import pytest

from app.services.telegram_api import TelegramAPIClient

TOKEN = "123456:ABC-def_ghi"


@pytest.fixture
def client(monkeypatch):
    c = TelegramAPIClient(TOKEN)
    calls: list[tuple[str, dict]] = []

    async def _rec(method, payload=None):
        calls.append((method, payload or {}))
        return {"message_id": 7} if method.startswith(("send", "edit")) else True

    monkeypatch.setattr(c, "_call", _rec)
    c.calls = calls  # type: ignore[attr-defined]
    return c


@pytest.mark.asyncio
async def test_set_webhook_minimal_omits_optionals(client):
    assert await client.set_webhook("https://hook.example/tg") is True
    method, payload = client.calls[-1]
    assert method == "setWebhook"
    assert "secret_token" not in payload and "allowed_updates" not in payload
    assert payload["url"] == "https://hook.example/tg"


@pytest.mark.asyncio
async def test_set_my_commands_without_scope(client):
    await client.set_my_commands([{"command": "start", "description": "Start"}])
    method, payload = client.calls[-1]
    assert method == "setMyCommands" and "scope" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn,field,method",
    [
        ("send_photo", "photo", "sendPhoto"),
        ("send_video", "video", "sendVideo"),
        ("send_document", "document", "sendDocument"),
    ],
)
async def test_media_send_minimal_and_full(client, fn, field, method):
    send = getattr(client, fn)
    out = await send(42, "https://cdn.example/f")
    assert out == {"message_id": 7}
    m, payload = client.calls[-1]
    assert m == method and payload[field] == "https://cdn.example/f"
    assert not ({"caption", "parse_mode", "reply_markup"} & payload.keys())

    markup = {"inline_keyboard": [[{"text": "Go", "url": "https://x"}]]}
    await send(42, "f", caption="c" * 2000, parse_mode="HTML", reply_markup=markup)
    _, payload = client.calls[-1]
    assert len(payload["caption"]) == 1024  # Telegram caption cap
    assert payload["parse_mode"] == "HTML" and payload["reply_markup"] == markup


@pytest.mark.asyncio
async def test_answer_callback_query_without_text(client):
    assert await client.answer_callback_query("cbq-1") is True
    method, payload = client.calls[-1]
    assert method == "answerCallbackQuery" and "text" not in payload


@pytest.mark.asyncio
async def test_unpin_without_message_id_unpins_latest(client):
    await client.unpin_chat_message(42)
    method, payload = client.calls[-1]
    assert method == "unpinChatMessage" and payload == {"chat_id": 42}


@pytest.mark.asyncio
async def test_edit_message_text_minimal(client):
    out = await client.edit_message_text(42, 9, "  new text  ")
    assert out == {"message_id": 7}
    method, payload = client.calls[-1]
    assert method == "editMessageText"
    assert payload == {"chat_id": 42, "message_id": 9, "text": "new text"}
