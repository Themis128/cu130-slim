"""Unit tests for Telegram webhook secret verify + chatbot routing."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import telegram as tg_api
from app.services import telegram_chatbot as bot


@pytest.mark.asyncio
async def test_webhook_rejects_bad_secret():
    account_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id,
        platform="telegram",
        meta_data={"webhook_secret": "expected-secret"},
        team_id=uuid.uuid4(),
        display_name="Bot",
        username="bot",
    )
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: account)
    db.execute = AsyncMock(return_value=result)

    request = AsyncMock()
    request.json = AsyncMock(return_value={"update_id": 1})

    with pytest.raises(HTTPException) as exc:
        await tg_api.receive_webhook(
            account_id,
            request,
            db,
            x_telegram_bot_api_secret_token="wrong-secret",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_accepts_matching_secret_and_ignores_non_text():
    account_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id,
        platform="telegram",
        meta_data={"webhook_secret": "expected-secret", "telegram_auto_reply": {"enabled": True}},
        team_id=uuid.uuid4(),
        display_name="Bot",
        username="bot",
    )
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: account)
    db.execute = AsyncMock(return_value=result)

    request = AsyncMock()
    request.json = AsyncMock(
        return_value={
            "update_id": 1,
            "message": {
                "message_id": 2,
                "sticker": {"emoji": "😀"},
                "chat": {"id": 99, "type": "private"},
                "from": {"id": 1, "is_bot": False},
            },
        }
    )

    out = await tg_api.receive_webhook(
        account_id,
        request,
        db,
        x_telegram_bot_api_secret_token="expected-secret",
    )
    assert out["status"] == "ok"
    assert out.get("ignored") is True


@pytest.mark.asyncio
async def test_webhook_routes_text_to_chatbot_and_sends():
    account_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id,
        platform="telegram",
        meta_data={
            "webhook_secret": "sec",
            "telegram_auto_reply": {"enabled": True, "system_prompt": "hi"},
            "bot_token_enc": "unused",
        },
        team_id=uuid.uuid4(),
        display_name="Cloudless",
        username="cloudless_bot",
        access_token_enc=b"x",
    )
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: account)
    db.execute = AsyncMock(return_value=result)

    request = AsyncMock()
    request.json = AsyncMock(
        return_value={
            "update_id": 9,
            "message": {
                "message_id": 11,
                "text": "Hello",
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 7, "first_name": "Ada", "is_bot": False},
            },
        }
    )

    fake_client = SimpleNamespace(send_message=AsyncMock(return_value={"message_id": 12}))

    with (
        patch(
            "app.services.telegram_chatbot.process_inbound_message",
            new=AsyncMock(return_value={"reply": "Hi Ada", "skipped": False, "reason": "", "intent": "greeting"}),
        ) as process,
        patch.object(tg_api, "_client_for", return_value=fake_client),
    ):
        out = await tg_api.receive_webhook(
            account_id,
            request,
            db,
            x_telegram_bot_api_secret_token="sec",
        )

    assert out.get("replied") is True
    process.assert_awaited_once()
    fake_client.send_message.assert_awaited_once()
    assert fake_client.send_message.await_args.args[0] == 555
    assert fake_client.send_message.await_args.args[1] == "Hi Ada"


@pytest.mark.asyncio
async def test_process_inbound_skips_when_disabled():
    with patch.object(bot, "store_message_memory", new=AsyncMock()) as store:
        out = await bot.process_inbound_message(
            account_id="a1",
            team_id=str(uuid.uuid4()),
            chat_id="99",
            sender_name="Ada",
            message_text="hi",
            message_id=1,
            config={"enabled": False},
            account_name="Cloudless",
        )
    assert out["skipped"] is True
    assert out["reason"] == "auto_reply_disabled"
    store.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_inbound_skips_cooldown():
    with (
        patch.object(bot, "store_message_memory", new=AsyncMock()),
        patch.object(bot, "check_cooldown", new=AsyncMock(return_value=False)),
    ):
        out = await bot.process_inbound_message(
            account_id="a1",
            team_id=str(uuid.uuid4()),
            chat_id="99",
            sender_name="Ada",
            message_text="hi",
            message_id=1,
            config={"enabled": True, "cooldown_seconds": 300},
            account_name="Cloudless",
        )
    assert out["skipped"] is True
    assert out["reason"] == "cooldown_active"


@pytest.mark.asyncio
async def test_process_inbound_skips_spam_by_default():
    with (
        patch.object(bot, "store_message_memory", new=AsyncMock()),
        patch.object(bot, "check_cooldown", new=AsyncMock(return_value=True)),
        patch.object(bot, "is_thread_paused", new=AsyncMock(return_value=False)),
        patch.object(bot, "detect_intent", new=AsyncMock(return_value=bot.INTENT_SPAM)),
    ):
        out = await bot.process_inbound_message(
            account_id="a1",
            team_id=str(uuid.uuid4()),
            chat_id="99",
            sender_name="Ada",
            message_text="BUY NOW FREE",
            message_id=1,
            config={"enabled": True, "reply_to_spam": False},
            account_name="Cloudless",
        )
    assert out["skipped"] is True
    assert out["reason"] == "spam_not_replied"
    assert out["intent"] == "spam"


@pytest.mark.asyncio
async def test_process_inbound_generates_reply_and_stores_memory():
    with (
        patch.object(bot, "store_message_memory", new=AsyncMock()) as store,
        patch.object(bot, "check_cooldown", new=AsyncMock(return_value=True)),
        patch.object(bot, "is_thread_paused", new=AsyncMock(return_value=False)),
        patch.object(bot, "detect_intent", new=AsyncMock(return_value="question")),
        patch.object(bot, "retrieve_brand_context", new=AsyncMock(return_value="")),
        patch.object(bot, "generate_contextual_reply", new=AsyncMock(return_value="Answer")),
        patch.object(bot, "set_cooldown", new=AsyncMock()) as cooldown,
    ):
        out = await bot.process_inbound_message(
            account_id="a1",
            team_id=str(uuid.uuid4()),
            chat_id="99",
            sender_name="Ada",
            message_text="What do you offer?",
            message_id=1,
            config={"enabled": True, "cooldown_seconds": 60},
            account_name="Cloudless",
        )
    assert out["skipped"] is False
    assert out["reply"] == "Answer"
    assert store.await_count == 2  # inbound + outbound
    cooldown.assert_awaited_once()
