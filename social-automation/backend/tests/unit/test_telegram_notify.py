"""Unit tests for app/services/telegram_notify.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import telegram_notify as notify


def _settings(**over):
    base = dict(
        TELEGRAM_NOTIFY_EMAIL="",
        DIGEST_EMAIL_TO="admin@cloudless.gr",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_fanout_success(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings())
    slack = AsyncMock(return_value=(True, None))
    email = AsyncMock()
    monkeypatch.setattr(notify, "post_telegram_to_slack", slack)
    monkeypatch.setattr(notify, "send_email", email)

    out = await notify.notify_channel_event(
        event="member_joined",
        chat_title="Cloudless HQ",
        chat_id=-100,
        actor="@jane",
        details=["via invite link: ig"],
    )
    assert out == {"slack": True, "email": True}
    text = slack.await_args.args[0] if slack.await_args.args else slack.await_args.kwargs["text"]
    assert "New member joined" in text
    assert "via invite link: ig" in text
    assert "-100" in text
    assert email.await_args.kwargs["to_addrs"] is None  # falls back to DIGEST_EMAIL_TO
    assert "[SocialAuto] Telegram: New member joined" in email.await_args.kwargs["subject"]


@pytest.mark.asyncio
async def test_custom_recipients(monkeypatch):
    monkeypatch.setattr(
        notify, "get_settings", lambda: _settings(TELEGRAM_NOTIFY_EMAIL="a@x.com, b@x.com")
    )
    monkeypatch.setattr(notify, "post_telegram_to_slack", AsyncMock(return_value=(True, None)))
    email = AsyncMock()
    monkeypatch.setattr(notify, "send_email", email)
    await notify.notify_channel_event(event="member_left", chat_title="HQ", chat_id=1)
    assert email.await_args.kwargs["to_addrs"] == ["a@x.com", "b@x.com"]


@pytest.mark.asyncio
async def test_failures_never_raise(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        notify, "post_telegram_to_slack", AsyncMock(return_value=(False, "no webhook"))
    )
    monkeypatch.setattr(notify, "send_email", AsyncMock(side_effect=RuntimeError("smtp down")))
    out = await notify.notify_channel_event(event="bot_admin", chat_title="", chat_id=-1)
    assert out == {"slack": False, "email": False}


@pytest.mark.asyncio
async def test_unknown_event_label_fallback(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: _settings())
    slack = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(notify, "post_telegram_to_slack", slack)
    monkeypatch.setattr(notify, "send_email", AsyncMock())
    await notify.notify_channel_event(event="some_new_thing", chat_title="HQ", chat_id=1)
    text = slack.await_args.args[0] if slack.await_args.args else slack.await_args.kwargs["text"]
    assert "Some New Thing" in text
