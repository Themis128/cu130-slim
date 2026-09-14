"""Unit tests for Telegram group watch helpers + webhook membership/owner link."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api import telegram as tg_api
from app.services import telegram_group_watch as gw
from app.services.telegram_api import extract_my_chat_member_update


def test_matching_keywords_and_mention():
    hits = gw.matching_keywords("What is the price in euros?", ["price", "τιμή"])
    assert "price" in hits
    assert gw.bot_mentioned(
        "hey @Cloudless_newbot help",
        [{"type": "mention", "offset": 4, "length": 17}],
        "Cloudless_newbot",
    )
    assert not gw.bot_mentioned("hello world", [], "Cloudless_newbot")


def test_build_digest_text_truncates_lines():
    msgs = [
        {"sender_name": "Ada", "text": "hi"},
        {"sender_name": "Bob", "text": "price?"},
    ]
    text = gw.build_digest_text(chat_title="Visibility Era 2.0", chat_id="-1001", messages=msgs)
    assert "Visibility Era 2.0" in text
    assert "Ada" in text
    assert "price?" in text


def test_extract_my_chat_member_update():
    event = extract_my_chat_member_update(
        {
            "update_id": 1,
            "my_chat_member": {
                "chat": {"id": -100, "type": "supergroup", "title": "Visibility Era 2.0"},
                "from": {"id": 9},
                "old_chat_member": {"status": "left"},
                "new_chat_member": {"status": "administrator"},
            },
        }
    )
    assert event is not None
    assert event["chat_id"] == -100
    assert event["new_status"] == "administrator"
    assert event["chat_title"] == "Visibility Era 2.0"


def test_should_auto_reply_in_group_requires_mention():
    cfg = {"auto_reply_groups_only_when_mentioned": True}
    inbound = {
        "chat_type": "supergroup",
        "text": "hello everyone",
        "entities": [],
    }
    assert not gw.should_auto_reply_in_group(cfg, inbound, "Cloudless_newbot")
    inbound["text"] = "@Cloudless_newbot hi"
    assert gw.should_auto_reply_in_group(cfg, inbound, "Cloudless_newbot")


@pytest.mark.asyncio
async def test_webhook_owner_link_command():
    account_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id,
        platform="telegram",
        meta_data={"webhook_secret": "sec", "bot_username": "Cloudless_newbot"},
        team_id=uuid.uuid4(),
        display_name="Cloudless",
        username="Cloudless_newbot",
        access_token_enc=b"x",
    )
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: account)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    request = AsyncMock()
    request.json = AsyncMock(
        return_value={
            "update_id": 2,
            "message": {
                "message_id": 3,
                "text": "/linkowner",
                "chat": {"id": 4242, "type": "private"},
                "from": {"id": 7, "username": "themis", "first_name": "T", "is_bot": False},
            },
        }
    )

    fake_client = SimpleNamespace(send_message=AsyncMock(return_value={"message_id": 1}))

    with (
        patch.object(tg_api, "_client_for", return_value=fake_client),
        patch.object(tg_api, "flag_modified"),
    ):
        out = await tg_api.receive_webhook(
            account_id,
            request,
            db,
            x_telegram_bot_api_secret_token="sec",
        )
    assert out.get("owner_linked") is True
    assert out.get("owner_chat_id") == "4242"
    fake_client.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_webhook_buffers_group_and_skips_unmentioned_reply():
    account_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id,
        platform="telegram",
        meta_data={
            "webhook_secret": "sec",
            "bot_username": "Cloudless_newbot",
            "telegram_auto_reply": {"enabled": True},
            "telegram_group_watch": {
                "enabled": True,
                "watch_all_groups": True,
                "owner_chat_id": "4242",
                "keywords": ["price"],
                "alert_on_keywords": True,
                "alert_on_bot_mention": True,
                "auto_reply_groups_only_when_mentioned": True,
                "watched_chats": [],
            },
        },
        team_id=uuid.uuid4(),
        display_name="Cloudless",
        username="Cloudless_newbot",
        access_token_enc=b"x",
    )
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: account)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    request = AsyncMock()
    request.json = AsyncMock(
        return_value={
            "update_id": 3,
            "message": {
                "message_id": 44,
                "text": "Anyone know the price?",
                "chat": {"id": -10099, "type": "supergroup", "title": "Visibility Era 2.0"},
                "from": {"id": 8, "first_name": "Ada", "is_bot": False},
            },
        }
    )

    fake_client = SimpleNamespace(
        send_message=AsyncMock(return_value={"message_id": 1}),
        forward_message=AsyncMock(return_value={}),
    )

    with (
        patch.object(tg_api, "_client_for", return_value=fake_client),
        patch.object(tg_api, "flag_modified"),
        patch(
            "app.services.telegram_group_watch.buffer_group_message",
            new=AsyncMock(),
        ),
        patch(
            "app.services.telegram_group_watch._alert_cooldown_ok",
            new=AsyncMock(return_value=True),
        ),
    ):
        out = await tg_api.receive_webhook(
            account_id,
            request,
            db,
            x_telegram_bot_api_secret_token="sec",
        )

    assert out.get("group_watch", {}).get("buffered") is True
    assert out.get("group_watch", {}).get("alerted") is True
    assert out.get("reason") == "group_requires_mention"
    assert out.get("auto_reply") is False
