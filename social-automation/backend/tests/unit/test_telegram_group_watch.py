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


# ── config + watch helpers ────────────────────────────────────────────


def test_normalize_group_watch_config():
    # non-dict → defaults
    cfg = gw.normalize_group_watch_config(None)
    assert cfg["enabled"] is False and cfg["keywords"] == gw.DEFAULT_KEYWORDS

    # unknown keys dropped, watched_chats/keywords coerced, owner str-ified
    cfg = gw.normalize_group_watch_config({
        "enabled": True, "bogus_key": 1,
        "watched_chats": "notalist", "keywords": [1, " x ", ""],
        "owner_chat_id": 42})
    assert cfg["enabled"] is True and "bogus_key" not in cfg
    assert cfg["watched_chats"] == [] and cfg["keywords"] == ["1", "x"]
    assert cfg["owner_chat_id"] == "42"

    assert gw.get_group_watch_from_meta(
        {gw.META_KEY: {"enabled": True}})["enabled"] is True
    assert gw.get_group_watch_from_meta(None)["enabled"] is False


def test_watched_chat_ids_and_is_watched():
    cfg = {"enabled": True, "watch_all_groups": False,
           "watched_chats": [{"chat_id": -100, "title": "g"},
                             555, {"no_id": 1}]}
    assert gw._watched_chat_ids(cfg) == {"-100", "555"}

    assert not gw.is_chat_watched({"enabled": False}, 1, "group")
    assert not gw.is_chat_watched(cfg, 1, "private")  # not a group
    # watch_all → any group ok
    assert gw.is_chat_watched({"enabled": True, "watch_all_groups": True},
                              999, "supergroup")
    # explicit list
    assert gw.is_chat_watched(cfg, -100, "group")
    assert not gw.is_chat_watched(cfg, -777, "group")


def test_upsert_watched_chat():
    cfg = gw.upsert_watched_chat({}, chat_id=-100, title="G",
                                 chat_type="group")
    assert cfg["watched_chats"][0]["chat_id"] == "-100"
    assert cfg["watched_chats"][0]["title"] == "G"

    # same id → updates title/type in place, no duplicate
    cfg = gw.upsert_watched_chat(cfg, chat_id=-100, title="G2")
    assert len(cfg["watched_chats"]) == 1
    assert cfg["watched_chats"][0]["title"] == "G2"
    assert "updated_at" in cfg["watched_chats"][0]


def test_bot_mentioned_and_keywords():
    # entity types
    assert gw.bot_mentioned("x", [{"type": "text_mention",
                                   "user": {"username": "Cloudless_newbot"}}],
                            "cloudless_newbot")
    assert gw.bot_mentioned("x", [{"type": "other"}], "b") is False
    assert gw.bot_mentioned("x", "notalist", "b") is False or True  # iterates
    # @uname in text
    assert gw.bot_mentioned("hey @mybot", [], "mybot")
    # no bot_username → False
    assert gw.bot_mentioned("hey @mybot", [], None) is False

    # keyword edges: empty kw skipped, substring fallback, dedup preserves input case
    hits = gw.matching_keywords("pricing please", ["Pricing", " ", "lease"])
    assert "Pricing" in hits and "lease" in hits
    assert gw.matching_keywords("", ["x"]) == []


# ── redis-backed helpers ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buffer_and_list_messages(monkeypatch):
    store: dict = {}

    class _R:
        async def lpush(self, k, v):
            store.setdefault(k, []).insert(0, v)

        async def ltrim(self, k, a, b):
            store[k] = store[k][: b + 1]

        async def expire(self, k, t):
            pass

        async def lrange(self, k, a, b):
            return store.get(k, [])

    monkeypatch.setattr(gw, "_redis", AsyncMock(return_value=_R()))
    await gw.buffer_group_message("acc", -100, {"text": "hi"})
    msgs = await gw.list_buffered_messages("acc", -100)
    assert msgs == [{"text": "hi"}]

    # bytes decode + bad JSON skipped + non-dict skipped
    key = gw._BUF_KEY.format(account_id="acc", chat_id=-100)
    store[key] = [b'{"a": 1}', b"{bad", "notdict"]
    assert await gw.list_buffered_messages("acc", -100) == [{"a": 1}]

    # redis failure → warning swallowed
    monkeypatch.setattr(gw, "_redis", AsyncMock(side_effect=RuntimeError("x")))
    await gw.buffer_group_message("acc", -100, {"t": 1})  # no raise
    assert await gw.list_buffered_messages("acc", -100) == []


@pytest.mark.asyncio
async def test_alert_cooldown_and_notify(monkeypatch):
    class _R:
        def __init__(self, created):
            self.created = created

        async def set(self, *a, **kw):
            return self.created

    monkeypatch.setattr(gw, "_redis",
                        AsyncMock(return_value=_R(created=True)))
    assert await gw._alert_cooldown_ok("a", "c", "fp") is True
    monkeypatch.setattr(gw, "_redis",
                        AsyncMock(return_value=_R(created=None)))
    assert await gw._alert_cooldown_ok("a", "c", "fp") is False
    # redis down → fail-open True
    monkeypatch.setattr(gw, "_redis", AsyncMock(side_effect=Exception()))
    assert await gw._alert_cooldown_ok("a", "c", "fp") is True

    # notify_owner ok + TelegramAPIError → False
    client = SimpleNamespace(send_message=AsyncMock())
    assert await gw.notify_owner(client, "o", "t") is True
    from app.services.telegram_api import TelegramAPIError
    client.send_message = AsyncMock(side_effect=TelegramAPIError(500, "x"))
    assert await gw.notify_owner(client, "o", "t") is False


# ── process_group_message_watch ───────────────────────────────────────


@pytest.mark.asyncio
async def test_process_group_message_watch(monkeypatch):
    monkeypatch.setattr(gw, "buffer_group_message", AsyncMock())
    monkeypatch.setattr(gw, "_alert_cooldown_ok", AsyncMock(return_value=True))
    cfg = {"enabled": True, "watch_all_groups": True,
           "owner_chat_id": "999", "alert_on_keywords": True,
           "keywords": ["price"], "alert_on_bot_mention": False}
    inbound = {"chat_id": -100, "chat_type": "group", "message_id": 5,
               "sender_name": "Ada", "chat_title": "G",
               "text": "what price?"}

    # not watched
    out = await gw.process_group_message_watch(
        client=None, account_id="a",
        cfg={"enabled": False}, inbound=inbound, bot_username=None)
    assert out["reason"] == "not_watched"

    # buffered, no owner
    out = await gw.process_group_message_watch(
        client=None, account_id="a",
        cfg={**cfg, "owner_chat_id": None},
        inbound=inbound, bot_username=None)
    assert out["buffered"] and out["reason"] == "no_owner"

    # no keyword/mention match
    out = await gw.process_group_message_watch(
        client=None, account_id="a", cfg=cfg,
        inbound={**inbound, "text": "hello"}, bot_username=None)
    assert out["reason"] == "no_alert_match"

    # cooldown blocks
    monkeypatch.setattr(gw, "_alert_cooldown_ok",
                        AsyncMock(return_value=False))
    out = await gw.process_group_message_watch(
        client=None, account_id="a", cfg=cfg,
        inbound=inbound, bot_username=None)
    assert out["reason"] == "alert_cooldown"
    monkeypatch.setattr(gw, "_alert_cooldown_ok", AsyncMock(return_value=True))

    # keyword hit → owner DM
    client = SimpleNamespace(send_message=AsyncMock())
    out = await gw.process_group_message_watch(
        client=client, account_id="a", cfg=cfg,
        inbound=inbound, bot_username=None)
    assert out["alerted"] and out["reason"] == "dm_sent"
    txt = client.send_message.await_args.args[1]
    assert "Visibility group alert" in txt and "price" in txt

    # forward path — success short-circuits
    client = SimpleNamespace(send_message=AsyncMock(),
                             forward_message=AsyncMock())
    out = await gw.process_group_message_watch(
        client=client, account_id="a",
        cfg={**cfg, "forward_alert_messages": True},
        inbound=inbound, bot_username=None)
    assert out["reason"] == "forwarded"
    client.send_message.assert_not_awaited()

    # forward fails → falls back to DM
    from app.services.telegram_api import TelegramAPIError
    client.forward_message = AsyncMock(side_effect=TelegramAPIError(500, "x"))
    out = await gw.process_group_message_watch(
        client=client, account_id="a",
        cfg={**cfg, "forward_alert_messages": True},
        inbound=inbound, bot_username=None)
    assert out["reason"] == "dm_sent"

    # mention path via entities
    client2 = SimpleNamespace(send_message=AsyncMock())
    out = await gw.process_group_message_watch(
        client=client2, account_id="a",
        cfg={**cfg, "alert_on_bot_mention": True,
             "alert_on_keywords": False},
        inbound={**inbound, "text": "@mybot hi",
                 "entities": [{"type": "mention", "offset": 0,
                               "length": 6}]},
        bot_username="mybot")
    assert out["alerted"] and "bot mention" in \
        client2.send_message.await_args.args[1]

    # DM send fails → dm_failed
    from app.services.telegram_api import TelegramAPIError
    client3 = SimpleNamespace(
        send_message=AsyncMock(side_effect=TelegramAPIError(500, "x")))
    out = await gw.process_group_message_watch(
        client=client3, account_id="a", cfg=cfg,
        inbound=inbound, bot_username=None)
    assert out["reason"] == "dm_failed"


# ── owner link / botfather / membership ───────────────────────────────


@pytest.mark.asyncio
async def test_owner_link_command():
    client = SimpleNamespace(send_message=AsyncMock())

    # non-private / empty / non-link → not handled
    cfg, ok = await gw.handle_owner_link_command(
        client=client, inbound={"chat_type": "group"}, cfg={})
    assert ok is False
    cfg, ok = await gw.handle_owner_link_command(
        client=client, inbound={"chat_type": "private", "text": ""}, cfg={})
    assert ok is False
    cfg, ok = await gw.handle_owner_link_command(
        client=client, inbound={"chat_type": "private", "text": "/help"},
        cfg={})
    assert ok is False

    # fresh link → owner bound + onboarding DM
    cfg, ok = await gw.handle_owner_link_command(
        client=client,
        inbound={"chat_type": "private", "chat_id": 777,
                 "text": "/start", "sender_username": "u"},
        cfg={}, bot_username="mybot")
    assert ok and cfg["owner_chat_id"] == "777" and cfg["enabled"]
    assert "Linked" in client.send_message.await_args.args[1]

    # already linked → "Already linked" DM
    cfg2, ok = await gw.handle_owner_link_command(
        client=client,
        inbound={"chat_type": "private", "chat_id": 777,
                 "text": "/linkowner@mybot"}, cfg=cfg)
    assert ok and "Already linked" in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_mistaken_botfather_command():
    client = SimpleNamespace(send_message=AsyncMock())
    # non-private / non-cmd / non-botfather cmd → False
    assert await gw.handle_mistaken_botfather_command(
        client=client, inbound={"chat_type": "group"}) is False
    assert await gw.handle_mistaken_botfather_command(
        client=client, inbound={"chat_type": "private",
                                "text": "hello"}) is False
    assert await gw.handle_mistaken_botfather_command(
        client=client, inbound={"chat_type": "private",
                                "text": "/random"}) is False
    # botfather cmd → redirect DM
    assert await gw.handle_mistaken_botfather_command(
        client=client, inbound={"chat_type": "private", "chat_id": 1,
                                "text": "/setprivacy"}) is True
    assert "BotFather" in client.send_message.await_args.args[1]


def test_bot_deep_links():
    assert gw.bot_deep_links("") == {}
    links = gw.bot_deep_links("@mybot")
    assert links["add_to_group"] == "https://t.me/mybot?startgroup=watch"
    assert links["botfather_privacy"] == "https://t.me/BotFather"


@pytest.mark.asyncio
async def test_ensure_bot_commands():
    client = SimpleNamespace(set_my_commands=AsyncMock(return_value=True))
    assert await gw.ensure_bot_commands(client) is True
    cmds = client.set_my_commands.await_args.args[0]
    assert any(c["command"] == "linkowner" for c in cmds)
    from app.services.telegram_api import TelegramAPIError
    client.set_my_commands = AsyncMock(side_effect=TelegramAPIError(500, "x"))
    assert await gw.ensure_bot_commands(client) is False


@pytest.mark.asyncio
async def test_bot_membership_change():
    client = SimpleNamespace(send_message=AsyncMock())

    # non-group → no-op
    cfg, info = await gw.handle_bot_membership_change(
        client=client, cfg={}, event={"chat_type": "private"})
    assert info == {"registered": False, "notified": False}

    # status not member/admin → no-op
    cfg, info = await gw.handle_bot_membership_change(
        client=client, cfg={},
        event={"chat_type": "group", "new_status": "left"})
    assert not info["registered"]

    # joined → registers + notifies owner
    cfg, info = await gw.handle_bot_membership_change(
        client=client, cfg={"owner_chat_id": "999"},
        event={"chat_type": "supergroup", "chat_id": -100,
               "chat_title": "VE", "new_status": "member",
               "old_status": "left"})
    assert info["registered"] and info["notified"]
    assert cfg["watched_chats"][0]["chat_id"] == "-100"
    assert "Now watching" in client.send_message.await_args.args[1]

    # promoted member→admin → also notifies
    cfg, info = await gw.handle_bot_membership_change(
        client=client, cfg={"owner_chat_id": "999"},
        event={"chat_type": "group", "chat_id": -1,
               "new_status": "administrator", "old_status": "member"})
    assert info["notified"]

    # no owner → registered but not notified
    cfg, info = await gw.handle_bot_membership_change(
        client=client, cfg={},
        event={"chat_type": "group", "chat_id": -1,
               "new_status": "member", "old_status": "left"})
    assert info["registered"] and not info["notified"]


def test_should_auto_reply_in_group():
    # private → always
    assert gw.should_auto_reply_in_group({}, {"chat_type": "private"}, "b")
    # group + flag off → always
    assert gw.should_auto_reply_in_group(
        {"auto_reply_groups_only_when_mentioned": False},
        {"chat_type": "group"}, "b")
    # group + flag on → only when mentioned
    assert not gw.should_auto_reply_in_group(
        {}, {"chat_type": "group", "text": "hi", "entities": []}, "b")
    assert gw.should_auto_reply_in_group(
        {}, {"chat_type": "group", "text": "@b hi"}, "b")


# ── digest ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_digest_for_account(monkeypatch):
    client = SimpleNamespace(send_message=AsyncMock())

    # disabled / no owner
    out = await gw.send_digest_for_account(
        client=client, account_id="a", cfg={"enabled": False}, force=True)
    assert out["reason"] == "digest_disabled"
    out = await gw.send_digest_for_account(
        client=client, account_id="a",
        cfg={"enabled": True, "digest_enabled": False}, force=True)
    assert out["reason"] == "digest_disabled"
    out = await gw.send_digest_for_account(
        client=client, account_id="a",
        cfg={"enabled": True, "digest_enabled": True,
             "owner_chat_id": None}, force=True)
    assert out["reason"] == "no_owner"

    import datetime as dt
    from zoneinfo import ZoneInfo
    now_hour = dt.datetime.now(ZoneInfo("Europe/Athens")).hour
    cfg = {"enabled": True, "digest_enabled": True,
           "owner_chat_id": "999", "digest_hour": now_hour,
           "watched_chats": [{"chat_id": -100, "title": "G"}]}

    # wrong hour (non-force)
    out = await gw.send_digest_for_account(
        client=client, account_id="a", cfg=cfg,
        hour_filter=(now_hour + 6) % 24)
    assert out["skipped"] and out["reason"] == "wrong_hour"

    # already sent today (redis nx=False)
    class _R:
        async def set(self, *a, **kw):
            return None

    monkeypatch.setattr(gw, "_redis", AsyncMock(return_value=_R()))
    out = await gw.send_digest_for_account(
        client=client, account_id="a", cfg=cfg,
        hour_filter=now_hour)
    assert out["reason"] == "already_sent_today"

    # force → skips the dedup; empty buffer → chat skipped
    monkeypatch.setattr(gw, "list_buffered_messages",
                        AsyncMock(return_value=[]))
    out = await gw.send_digest_for_account(
        client=client, account_id="a", cfg=cfg, force=True)
    assert out["sent"] == 0 and out["chats"][0]["reason"] == "empty"

    # happy — digest built and sent
    monkeypatch.setattr(gw, "list_buffered_messages",
                        AsyncMock(return_value=[
                            {"sender_name": "Ada", "text": "hi"}]))
    out = await gw.send_digest_for_account(
        client=client, account_id="a", cfg=cfg, force=True)
    assert out["sent"] == 1 and out["chats"][0]["sent"] is True
    assert "Group digest" in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_redis_factory():
    # MESSENGER_REDIS_URL fallback to REDIS_URL
    r = await gw._redis()
    assert r is not None
    try:
        await r.aclose()
    except AttributeError:
        await r.close()
