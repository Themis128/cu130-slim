"""Unit tests for app/worker/tasks/mastersheet_watch.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.worker.tasks import mastersheet_watch as W


def _resp(status=200, payload=None, text=""):
    req = httpx.Request("POST", "http://b")
    return httpx.Response(status, json=payload if payload is not None else {}, request=req) if not text else httpx.Response(status, text=text, request=req)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(W.time, "sleep", lambda s: None)


def test_bridge_ok_and_retries(monkeypatch, no_sleep):
    seq = [_resp(409), _resp(500, text="Execution context was destroyed"), _resp(200, {"result": 1})]
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(url)
        return seq.pop(0)

    monkeypatch.setattr(W.httpx, "post", post)
    assert W._bridge("/session/evaluate", {}) == {"result": 1}
    assert calls == [f"{W.BRIDGE}/session/evaluate"] * 3


def test_bridge_exhausted_returns_empty(monkeypatch, no_sleep):
    monkeypatch.setattr(W.httpx, "post", lambda *a, **k: _resp(409))
    assert W._bridge("/x", {}, tries=2) == {}


def test_bridge_http_error_retries_then_raises(monkeypatch, no_sleep):
    seq = [_resp(502), _resp(502)]
    monkeypatch.setattr(W.httpx, "post", lambda *a, **k: seq.pop(0))
    with pytest.raises(httpx.HTTPStatusError):
        W._bridge("/x", {}, tries=2)


def test_ev(monkeypatch):
    monkeypatch.setattr(W, "_bridge", lambda path, data: {"result": data["expression"]})
    assert W._ev("1+1") == "1+1"


def test_fetch_renders(monkeypatch, no_sleep):
    paths = []

    def bridge(path, data):
        paths.append(path)
        if path == "/session/close":
            raise RuntimeError("no session")
        return {}

    results = iter([False, True, "Mastersheet\nWeek 1"])
    monkeypatch.setattr(W, "_bridge", bridge)
    monkeypatch.setattr(W, "_ev", lambda expr: next(results))
    out = W._fetch()
    assert out["text"] == "Mastersheet\nWeek 1" and len(out["hash"]) == 64
    assert paths == ["/session/close", "/session/start", "/session/navigate"]


def test_fetch_never_renders(monkeypatch, no_sleep):
    monkeypatch.setattr(W, "_bridge", lambda path, data: {})
    monkeypatch.setattr(W, "_ev", lambda expr: False)
    with pytest.raises(RuntimeError, match="never rendered"):
        W._fetch()


def test_fetch_empty_text(monkeypatch, no_sleep):
    results = iter([True, None])
    monkeypatch.setattr(W, "_bridge", lambda path, data: {})
    monkeypatch.setattr(W, "_ev", lambda expr: next(results))
    assert W._fetch()["text"] == ""


def test_keywords_and_classify():
    assert W._keywords(["Growth growth funnel the and", "funnel Growth"])[:2] == ["growth", "funnel"]
    assert W._classify_delta("", "x")["kind"] == ["first_snapshot"]
    same = W._classify_delta("a\nb", "a\nb")
    assert same == {"changed": False, "kind": [], "added": [], "keywords": []}
    d = W._classify_delta("intro", "intro\nWeek 3 recording youtu.be/x\nHomework: ship landing page\nok")
    assert d["changed"] is True
    assert d["kind"] == ["new_homework", "new_recording", "new_week"]
    assert "ok" not in d["added"]
    edit = W._classify_delta("intro", "intro\nplain new paragraph")
    assert edit["kind"] == ["edit"]


def _acc(meta=None, token_enc=b"tok"):
    return SimpleNamespace(meta_data=meta or {}, access_token_enc=token_enc, team_id="team-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meta,expected",
    [
        ({"bot_token_enc": "str-enc"}, b"str-enc"),
        ({"bot_token_enc": b"bytes-enc"}, b"bytes-enc"),
        ({}, b"tok"),
    ],
)
async def test_notify_token_sources(monkeypatch, meta, expected):
    db = MagicMock()
    res = MagicMock()
    res.scalar_one.return_value = _acc(meta)
    db.execute = AsyncMock(return_value=res)
    seen = []
    monkeypatch.setattr(W, "decrypt_token", lambda b: seen.append(b) or "T")
    client = MagicMock()
    client.send_message = AsyncMock()
    monkeypatch.setattr(W, "TelegramAPIClient", lambda tok: client)
    await W._notify(db, "hi")
    assert seen == [expected]
    client.send_message.assert_awaited_once_with(W.TELEGRAM_CHAT, "hi")


def _db(last, acc):
    db = MagicMock()
    r_last, r_acc = MagicMock(), MagicMock()
    r_last.scalar_one_or_none.return_value = last
    r_acc.scalar_one.return_value = acc
    db.execute = AsyncMock(side_effect=[r_last, r_acc])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_run_changed_notifies(monkeypatch):
    monkeypatch.setattr(W, "_fetch", lambda: {"text": "intro\nWeek 2 slides canva.link/x", "hash": "h" * 64})
    last = SimpleNamespace(team_id="team-last", meta_data={"text": "intro"})
    db = _db(last, _acc())
    monkeypatch.setattr(W, "task_session", lambda: db)
    notify = AsyncMock()
    monkeypatch.setattr(W, "_notify", notify)
    out = await W._run()
    assert out == {"changed": True, "hash": "h" * 12, "delta": ["new_slides", "new_week"]}
    assert db.add.call_count == 2
    events = [c.args[0] for c in db.add.call_args_list]
    assert events[0].team_id == "team-last" and events[1].event_type == "coaching_action"
    assert "new_slides + new_week" in notify.await_args.args[1]


@pytest.mark.asyncio
async def test_run_unchanged_first_uses_account_team(monkeypatch):
    monkeypatch.setattr(W, "_fetch", lambda: {"text": "same", "hash": "a" * 64})
    last = SimpleNamespace(team_id="t", meta_data={"text": "same"})
    db = _db(last, _acc())
    monkeypatch.setattr(W, "task_session", lambda: db)
    notify = AsyncMock()
    monkeypatch.setattr(W, "_notify", notify)
    out = await W._run()
    assert out["changed"] is False and out["delta"] == []
    assert db.add.call_count == 1
    notify.assert_not_awaited()

    db = _db(None, _acc())
    monkeypatch.setattr(W, "task_session", lambda: db)
    out = await W._run()
    assert out["delta"] == ["first_snapshot"]
    assert db.add.call_args_list[0].args[0].team_id == "team-1"


def test_task_wrapper(monkeypatch):
    monkeypatch.setattr(W, "_run", AsyncMock(return_value={"changed": False}))
    monkeypatch.setattr(W, "run_async", lambda coro: asyncio.run(coro))
    assert W.watch_mastersheet.run() == {"changed": False}
