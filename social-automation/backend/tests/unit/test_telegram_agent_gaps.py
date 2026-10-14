"""Coverage-gap tests for app/services/telegram_agent.py — the branches the
main suite doesn't reach: image/copy generation internals, _bot_token
fallbacks, welcome failure, pillar-stat refresh, and sweep error handling.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.telegram_agent as TA
from app.core.security import encrypt_token
from app.models.social_account import SocialAccount
from app.services.telegram_api import TelegramAPIError


def _account(meta=None, **kw) -> SocialAccount:
    return SocialAccount(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform="telegram",
        status="active",
        access_token_enc=kw.pop("access_token_enc", encrypt_token("bot-tok")),
        meta_data=meta or {},
        **kw,
    )


def _managed(chat_id="-1004436942504", title="Cloudless — Automation HQ"):
    return {"chat_id": chat_id, "title": title}


class _DB:
    def __init__(self, results=None):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self._results = list(results or [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, *a, **kw):
        if self._results:
            return self._results.pop(0)
        return SimpleNamespace(all=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: []))


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, token, fail=None):
        self.token = token
        self.fail = fail
        self.sent = []

    async def send_photo(self, chat_id, photo, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("photo", photo))
        return {"message_id": 101}

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("message", text))
        return {"message_id": 102}

    async def send_message_with_markup(self, chat_id, text, reply_markup=None, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("markup", text, reply_markup))
        return {"message_id": 103}

    async def send_document(self, chat_id, doc, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(("document", doc))
        return {"message_id": 104}

    async def send_poll(self, chat_id, question, options, **kw):
        self.sent.append(("poll", question, options))
        return {"message_id": 105}

    async def pin_chat_message(self, chat_id, mid):
        self.sent.append(("pin", mid))
        return {"ok": True}


# ── _generate_image ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_image_public_url(monkeypatch):
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(return_value={"image_base64": base64.b64encode(b"png").decode()}),
    )
    asset = SimpleNamespace(id=uuid.uuid4(), public_url="https://cdn/x.png", storage_path=None)
    monkeypatch.setattr(
        "app.services.media_storage.persist_generated_image", AsyncMock(return_value=asset)
    )
    db = _DB()
    out = await TA._generate_image(db, _account(meta={"telegram_channel": _managed()}), "p")
    assert out == "https://cdn/x.png"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_generate_image_storage_path_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(return_value={"image_base64": base64.b64encode(b"png").decode()}),
    )
    asset = SimpleNamespace(id=uuid.uuid4(), public_url=None, storage_path="media/x.png")
    monkeypatch.setattr(
        "app.services.media_storage.persist_generated_image", AsyncMock(return_value=asset)
    )
    monkeypatch.setattr(
        "app.services.publishing._media_public_url", lambda p: f"https://pub/{p}"
    )
    out = await TA._generate_image(_DB(), _account(meta={"telegram_channel": _managed()}), "p")
    assert out == "https://pub/media/x.png"


@pytest.mark.asyncio
async def test_generate_image_no_b64_and_exception(monkeypatch):
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(return_value={}),
    )
    assert await TA._generate_image(_DB(), _account(meta={"telegram_channel": _managed()}), "p") is None

    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(side_effect=RuntimeError("cf down")),
    )
    db = _DB()
    assert await TA._generate_image(db, _account(meta={"telegram_channel": _managed()}), "p") is None
    assert db.rollbacks == 1


# ── _generate_copy ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_copy_pipeline(monkeypatch):
    monkeypatch.setattr(
        TA, "call_inference", AsyncMock(return_value={"content": "draft"})
    )
    quality = SimpleNamespace(content="polished")
    monkeypatch.setattr(
        "app.services.quality_pipeline.apply_quality_pipeline",
        AsyncMock(return_value=quality),
    )
    out = await TA._generate_copy(_DB(), _account(), "prompt")
    assert out == "polished"


@pytest.mark.asyncio
async def test_generate_copy_empty_raises(monkeypatch):
    monkeypatch.setattr(TA, "call_inference", AsyncMock(return_value={"content": "  "}))
    with pytest.raises(RuntimeError, match="empty generation"):
        await TA._generate_copy(_DB(), _account(), "p")


# ── _bot_token ────────────────────────────────────────────────────────────


def test_bot_token_meta_first():
    acc = _account(meta={"bot_token_enc": encrypt_token("meta-tok")})
    assert TA._bot_token(acc) == "meta-tok"


def test_bot_token_access_token_fallback():
    acc = _account(meta={})
    assert TA._bot_token(acc) == "bot-tok"


def test_bot_token_bad_meta_falls_through_to_access():
    acc = _account(meta={"bot_token_enc": b"garbage"})
    assert TA._bot_token(acc) == "bot-tok"


def test_bot_token_all_bad_returns_empty():
    acc = _account(meta={"bot_token_enc": b"garbage"}, access_token_enc=b"garbage")
    assert TA._bot_token(acc) == ""


@pytest.mark.asyncio
async def test_run_channel_agent_no_token():
    acc = _account(
        meta={"telegram_channel": _managed()},
        access_token_enc=b"garbage",
    )
    out = await TA.run_channel_agent(acc, _DB())
    assert out == {"skipped": "no_token"}


# ── run_channel_agent branches ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_long_copy_sends_followup_message(monkeypatch):
    acc = _account(meta={"telegram_channel": _managed()})
    monkeypatch.setattr(
        TA, "plan_post",
        lambda now, stats=None: {
            "pillar": "authority", "key": None, "series_tag": "[Playbook]",
            "prompt": "p", "image_prompt": "ip", "buttons": None,
            "document": None, "pin": False,
        },
    )
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="x" * 1500))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value=None))
    monkeypatch.setattr(TA, "TelegramAPIClient", _FakeClient)
    out = await TA.run_channel_agent(acc, _DB())
    assert out["published"]["kind"] == "message"
    assert len(out["published"]["message_ids"]) == 2  # caption + remainder


@pytest.mark.asyncio
async def test_pin_failure_is_tolerated(monkeypatch):
    acc = _account(meta={"telegram_channel": _managed()})
    monkeypatch.setattr(
        TA, "plan_post",
        lambda now, stats=None: {
            "pillar": "offer", "key": "audit", "series_tag": "[Deal]",
            "prompt": "p", "image_prompt": "ip", "buttons": None,
            "document": None, "pin": True,
        },
    )
    monkeypatch.setattr(TA, "_generate_copy", AsyncMock(return_value="short copy"))
    monkeypatch.setattr(TA, "_generate_image", AsyncMock(return_value="https://img"))

    class _PinFailClient(_FakeClient):
        async def pin_chat_message(self, chat_id, mid):
            raise RuntimeError("pin denied")

    monkeypatch.setattr(TA, "TelegramAPIClient", _PinFailClient)
    out = await TA.run_channel_agent(acc, _DB())
    assert out["published"]["kind"] == "photo"  # publish still succeeded


# ── _weekly_poll disabled ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weekly_poll_disabled():
    client = _FakeClient("t")
    out = await TA._weekly_poll(client, "-100", {"polls_enabled": False}, datetime.now(UTC))
    assert out is None and client.sent == []


# ── maybe_welcome_member failure ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_welcome_send_failure_rolls_back():
    acc = _account(meta={"telegram_channel": _managed()})
    db = _DB()
    client = _FakeClient("t", fail=TelegramAPIError(400, "bad"))
    ok = await TA.maybe_welcome_member(client, acc, {"first_name": "N"}, db)
    assert ok is False and db.rollbacks == 1


@pytest.mark.asyncio
async def test_welcome_no_channel_returns_false():
    acc = _account(meta={})  # no managed channel
    ok = await TA.maybe_welcome_member(_FakeClient("t"), acc, {"first_name": "N"}, _DB())
    assert ok is False


# ── refresh_pillar_stats ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_pillar_stats_counts_joins():
    now = datetime.now(UTC)
    rows = [
        (now - timedelta(hours=2), "channel_post", {"tag": "authority"}),
        (now - timedelta(hours=1), "member_joined", {}),
        (now - timedelta(hours=1), "member_joined", {}),
        (now - timedelta(days=2), "channel_post", {"kind": "proof"}),
    ]
    db = _DB(results=[SimpleNamespace(all=lambda: rows)])
    acc = _account(meta={"telegram_channel": _managed()})
    stats = await TA.refresh_pillar_stats(acc, db)
    assert stats["authority"]["posts"] == 1 and stats["authority"]["joins"] == 2
    assert stats["proof"]["joins"] == 0  # join window expired
    assert acc.meta_data[TA.AGENT_META_KEY]["pillar_stats"] == stats


# ── run_agent_sweep error path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_account_error_alerts_slack(monkeypatch):
    acc = _account(meta={"telegram_channel": _managed()})
    db = _DB(results=[SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [acc]))])

    import app.worker._async as wa
    monkeypatch.setattr(wa, "task_session", lambda: _Ctx(db))
    monkeypatch.setattr(TA, "refresh_pillar_stats", AsyncMock(side_effect=RuntimeError("db gone")))

    slack = AsyncMock()
    monkeypatch.setattr(
        "app.services.slack_notifications.post_alert_to_slack", slack
    )
    out = await TA.run_agent_sweep()
    assert out["accounts"] == 1 and out["published"] == 0
    assert "error" in out["results"][0]
    slack.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_slack_alert_failure_tolerated(monkeypatch):
    acc = _account(meta={"telegram_channel": _managed()})
    db = _DB(results=[SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [acc]))])

    import app.worker._async as wa
    monkeypatch.setattr(wa, "task_session", lambda: _Ctx(db))
    monkeypatch.setattr(TA, "refresh_pillar_stats", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(
        "app.services.slack_notifications.post_alert_to_slack",
        AsyncMock(side_effect=RuntimeError("slack down")),
    )
    out = await TA.run_agent_sweep()
    assert out["results"][0]["error"]  # still recorded; alert failure swallowed
