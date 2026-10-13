"""Unit tests for app/services/telegram_agent.py (channel autopilot)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.telegram_agent as TA
from app.models.social_account import SocialAccount
from app.services.telegram_api import TelegramAPIError, extract_chat_join_request


def _account(meta=None, **kw) -> SocialAccount:
    acct = SocialAccount(
        id=kw.pop("id", uuid.uuid4()),
        team_id=uuid.uuid4(),
        platform="telegram",
        status="active",
        access_token_enc=b"enc",
        meta_data=meta or {},
        **kw,
    )
    return acct


def _managed(chat_id="-1004436942504", title="Cloudless — Automation HQ"):
    return {"chat_id": chat_id, "title": title}


class _DB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def execute(self, *a, **kw):
        return SimpleNamespace(
            all=lambda: [],
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )


class _FakeClient:
    """Captures Telegram sends without touching the network."""

    def __init__(self, token, fail: TelegramAPIError | None = None):
        self.token = token
        self.fail = fail
        self.sent = []

    async def send_photo(self, chat_id, photo, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("photo", chat_id, photo, kw))
        return {"message_id": 100 + len(self.sent)}

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("message", chat_id, text, kw))
        return {"message_id": 100 + len(self.sent)}

    async def send_message_with_markup(self, chat_id, text, reply_markup, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("markup", chat_id, text, reply_markup))
        return {"message_id": 100 + len(self.sent)}

    async def send_poll(self, chat_id, question, options, **kw):
        self.sent.append(("poll", chat_id, question, options))
        return {"message_id": 100 + len(self.sent)}

    async def send_document(self, chat_id, document, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("document", chat_id, document, kw))
        return {"message_id": 100 + len(self.sent)}

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.sent.append(("pin", chat_id, message_id, kw))
        return True


def _client_holder():
    holder = {}

    def factory(token, *a, **kw):
        client = _FakeClient(token)
        holder["client"] = client
        return client

    return holder, factory


# ── planning ─────────────────────────────────────────────────────────────


def test_plan_post_rotation():
    mon = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)  # Monday, week 42
    assert (plan := TA.plan_post(mon))
    assert plan["pillar"] == "authority" and plan["series_tag"] == "[Playbook]"

    wed = datetime(2026, 10, 14, 11, 0, tzinfo=UTC)
    assert TA.plan_post(wed)["pillar"] == "engagement"

    # Tue/Thu/Sat/Sun are rest/poll days
    for day in (13, 15, 17, 18):
        assert TA.plan_post(datetime(2026, 10, day, 11, 0, tzinfo=UTC)) is None


def test_plan_post_friday_offer_and_adaptation():
    # Week 45 (2026-11-06 is a Friday, ISO week 45) → 45 % 3 == 0 → offer
    fri_offer = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
    plan = TA.plan_post(fri_offer)
    assert plan["pillar"] == "offer" and plan["buttons"]

    # Week 44 Friday (2025-10-31) → 44 % 3 == 2 → adaptive pick
    fri = datetime(2025, 10, 31, 11, 0, tzinfo=UTC)
    stats = {
        "authority": {"posts": 5, "joins": 1},
        "proof": {"posts": 4, "joins": 8},
    }
    plan = TA.plan_post(fri, stats)
    assert plan["pillar"] == "proof"  # best joins-per-post

    # Below the min-post threshold → fixed fallback
    stats = {"authority": {"posts": 1, "joins": 50}}
    assert TA.plan_post(fri, stats)["pillar"] in ("authority", "proof")


def test_pillar_stats_attribution():
    t0 = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
    from datetime import timedelta

    events = [
        (t0, {"_event": "channel_post", "tag": "authority"}),
        (t0 + timedelta(hours=2), {"_event": "member_joined"}),   # inside 24h
        (t0 + timedelta(hours=30), {"_event": "member_joined"}),  # outside
        (t0 + timedelta(days=2), {"_event": "channel_post", "tag": "authority"}),
    ]
    stats = TA._pillar_stats(events)
    assert stats["authority"] == {"posts": 2, "joins": 1}


def test_agent_enabled():
    assert not TA.agent_enabled(_account(meta={}))
    assert TA.agent_enabled(_account(meta={"telegram_channel": _managed()}))
    assert not TA.agent_enabled(
        _account(
            meta={
                "telegram_channel": _managed(),
                "telegram_agent": {"enabled": False},
            }
        )
    )


# ── daily run ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_publishes_photo_post(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="Hello channel"))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value="https://x/img.jpg"))
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 12, 11, tzinfo=UTC)))

    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())

    assert out["published"]["pillar"] == "authority"
    client = holder["client"]
    kind, chat_id, photo, kw = client.sent[0]
    assert kind == "photo" and photo == "https://x/img.jpg"
    assert kw["caption"] == "Hello channel"


@pytest.mark.asyncio
async def test_run_idempotent_same_day(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 12, 11, tzinfo=UTC)))

    acct = _account(
        meta={
            "telegram_channel": _managed(),
            "telegram_agent": {"last_post_date": "2026-10-12"},
        }
    )
    out = await TA.run_channel_agent(acct, _DB())
    assert out == {"skipped": "already_posted"}
    assert holder["client"].sent == []


@pytest.mark.asyncio
async def test_run_rest_day_and_disabled(monkeypatch):
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "TelegramAPIClient", _FakeClient)
    # Tuesday 2026-10-13
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 13, 11, tzinfo=UTC)))
    acct = _account(meta={"telegram_channel": _managed()})
    assert await TA.run_channel_agent(acct, _DB()) == {"skipped": "rest_day"}

    disabled = _account(meta={"telegram_channel": _managed(), "telegram_agent": {"enabled": False}})
    assert await TA.run_channel_agent(disabled, _DB()) == {"skipped": "disabled_or_no_channel"}
    assert await TA.run_channel_agent(_account(meta={}), _DB()) == {"skipped": "disabled_or_no_channel"}


@pytest.mark.asyncio
async def test_run_telegram_error(monkeypatch):
    err = TelegramAPIError(403, "bot was kicked", method="sendPhoto")
    monkeypatch.setattr(TA, "TelegramAPIClient", lambda t: _FakeClient(t, fail=err))
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="x"))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value="https://x/i.jpg"))
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 12, 11, tzinfo=UTC)))
    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())
    assert out["error"].startswith("telegram send failed (403)")


@pytest.mark.asyncio
async def test_run_no_image_falls_back_to_text(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="text only"))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value=None))
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 12, 11, tzinfo=UTC)))
    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())
    assert out["published"]["kind"] == "message"
    assert holder["client"].sent[0][0] == "markup"


@pytest.mark.asyncio
async def test_sunday_poll(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    # Sunday 2026-10-18, ISO week 42
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 18, 11, tzinfo=UTC)))
    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())
    assert "poll" in out
    assert holder["client"].sent[0][0] == "poll"
    assert acct.meta_data["telegram_agent"]["last_poll_week"] == 42

    # Second call same week → no duplicate poll
    out2 = await TA.run_channel_agent(acct, _DB())
    assert "poll" not in out2


@pytest.mark.asyncio
async def test_offer_buttons_inline_markup(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="deal"))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value=None))
    # Friday week 45 → offer pillar
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 11, 6, 11, tzinfo=UTC)))
    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())
    assert out["published"]["pillar"] == "offer"
    markup = holder["client"].sent[0][3]
    assert markup["inline_keyboard"][0][0]["url"].startswith("https://")


@pytest.mark.asyncio
async def test_checklist_offer_sends_pdf_and_pins(monkeypatch):
    holder, factory = _client_holder()
    monkeypatch.setattr(TA, "TelegramAPIClient", factory)
    monkeypatch.setattr(TA, "_bot_token", lambda a: "tok")
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="grab the pdf"))
    gen_image = AsyncMock()
    monkeypatch.setattr(TA, "_generate_image", gen_image)

    # Find an offer Friday whose rotation lands on the "checklist" offer
    # (week_num = ISO week // 3; checklist is OFFERS index 2).
    target = None
    for wk in range(1, 53):
        if wk % 3 != 0:
            continue
        if (wk // 3) % len(TA.OFFERS) == 2:
            target = wk
            break
    assert target is not None

    # First Friday of ISO week `target` in 2026
    day = datetime.fromisocalendar(2026, target, 5).replace(tzinfo=UTC)
    monkeypatch.setattr(TA, "datetime", SimpleNamespace(now=lambda tz: day))

    acct = _account(meta={"telegram_channel": _managed()})
    out = await TA.run_channel_agent(acct, _DB())

    assert out["published"]["pillar"] == "offer"
    kinds = [s[0] for s in holder["client"].sent]
    assert kinds[0] == "document"
    assert holder["client"].sent[0][2].endswith("automation-checklist.pdf")
    assert "pin" in kinds  # offers get pinned
    gen_image.assert_not_called()  # document posts skip the FLUX call


# ── engagement ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_welcome_throttle_and_send(monkeypatch):
    acct = _account(meta={"telegram_channel": _managed()})
    client = _FakeClient("tok")
    event = {"first_name": "Themis", "invite_link_name": "ig"}
    db = _DB()

    assert await TA.maybe_welcome_member(client, acct, event, db) is True
    assert client.sent[0][0] == "message"
    assert "Themis" in client.sent[0][2]
    assert "via ig" in client.sent[0][2]

    # Second welcome same day allowed; third is throttled
    assert await TA.maybe_welcome_member(client, acct, event, db) is True
    assert await TA.maybe_welcome_member(client, acct, event, db) is False
    assert len(client.sent) == 2


@pytest.mark.asyncio
async def test_welcome_disabled(monkeypatch):
    acct = _account(
        meta={
            "telegram_channel": _managed(),
            "telegram_agent": {"welcome_enabled": False},
        }
    )
    client = _FakeClient("tok")
    assert await TA.maybe_welcome_member(client, acct, {}, _DB()) is False
    assert client.sent == []


# ── join-request parsing + webhook helpers ───────────────────────────────


def test_extract_chat_join_request():
    update = {
        "update_id": 7,
        "chat_join_request": {
            "chat": {"id": -1004436942504, "type": "channel", "title": "HQ"},
            "from": {"id": 42, "username": "themis", "first_name": "Themis"},
            "invite_link": {"name": "ig", "invite_link": "https://t.me/+x"},
            "bio": "self-hoster",
        },
    }
    parsed = extract_chat_join_request(update)
    assert parsed["chat_id"] == -1004436942504
    assert parsed["user_id"] == 42
    assert parsed["invite_link_name"] == "ig"
    assert extract_chat_join_request({}) is None
    assert extract_chat_join_request({"chat_join_request": {"chat": {}}}) is None


def test_allowed_updates_includes_join_request():
    from app.services.telegram_api import TELEGRAM_ALLOWED_UPDATES

    assert "chat_join_request" in TELEGRAM_ALLOWED_UPDATES


# ── sweep ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_sweep(monkeypatch):
    acct_on = _account(meta={"telegram_channel": _managed()})
    acct_off = _account(meta={"telegram_channel": _managed(), "telegram_agent": {"enabled": False}})

    class _Sess:
        async def __aenter__(self):
            return _SweepDB([acct_on, acct_off])

        async def __aexit__(self, *a):
            return False

    class _SweepDB(_DB):
        def __init__(self, rows):
            super().__init__()
            self._rows = rows

        async def execute(self, *a, **kw):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: self._rows),
                all=lambda: [],
            )

    import app.worker._async as WA

    monkeypatch.setattr(WA, "task_session", lambda: _Sess())
    monkeypatch.setattr(TA, "refresh_pillar_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(TA, "run_channel_agent", AsyncMock(return_value={"published": {"pillar": "authority"}}))

    out = await TA.run_agent_sweep()
    assert out["accounts"] == 1 and out["published"] == 1
    assert out["results"][0]["account_id"] == str(acct_on.id)
