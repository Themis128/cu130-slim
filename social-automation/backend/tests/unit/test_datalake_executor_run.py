"""datalake_executor: per-team signal fan-out, dedup-on-success, dispatch loop."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.tasks import datalake_executor as DX


def _db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


def _scalars_result(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalar_one.return_value = value
    return r


def _ev(meta):
    ev = MagicMock()
    ev.meta_data = meta
    return ev


# ---------- _pending_signals ----------


@pytest.mark.asyncio
async def test_pending_signals_empty():
    db = _db()
    db.execute = AsyncMock(return_value=_scalars_result([]))
    assert await DX._pending_signals(db) == []


@pytest.mark.asyncio
async def test_pending_signals_dedupes_per_team_latest_wins():
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    db = _db()
    # ordered occurred_at desc — first row per team is the latest
    db.execute = AsyncMock(
        return_value=_scalars_result(
            [
                _ev({"team_id": t1, "actions": [{"type": "platform_focus", "platform": "new"}]}),
                _ev({"team_id": t2, "actions": [{"type": "platform_focus", "platform": "b"}]}),
                _ev({"team_id": t1, "actions": [{"type": "platform_focus", "platform": "old"}]}),
                _ev({"actions": [{"type": "platform_focus", "platform": "orphan"}]}),  # no team_id
                _ev({}),  # empty meta
            ]
        )
    )
    out = await DX._pending_signals(db)
    assert out == [
        (t1, {"team_id": t1, "actions": [{"type": "platform_focus", "platform": "new"}]}),
        (t2, {"team_id": t2, "actions": [{"type": "platform_focus", "platform": "b"}]}),
    ]


# ---------- _already_executed ----------


@pytest.mark.asyncio
async def test_already_executed_true_and_false():
    db = _db()
    team = uuid.uuid4()
    db.execute = AsyncMock(return_value=_scalar_result(1))
    assert await DX._already_executed(db, team, "content_topic", "AI") is True
    db.execute = AsyncMock(return_value=_scalar_result(None))
    assert await DX._already_executed(db, team, "content_topic", "AI") is False


@pytest.mark.asyncio
async def test_already_executed_sql_excludes_error_results_and_scopes_team():
    db = _db()
    team = uuid.uuid4()
    db.execute = AsyncMock(return_value=_scalar_result(None))
    await DX._already_executed(db, team, "devapp_health", "x:quota")
    sql, params = db.execute.await_args.args
    assert "meta_data->'result'->>'error' IS NULL" in str(sql)
    assert "team_id=:team" in str(sql)
    assert params["team"] == team
    assert params["a"] == "devapp_health"
    assert params["k"] == "x:quota"


# ---------- executors ----------


@pytest.mark.asyncio
async def test_content_topic_creates_draft(monkeypatch):
    team = uuid.uuid4()
    db = _db()
    user_id = uuid.uuid4()
    db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(user_id)])
    out = await DX._exec_content_topic(db, team, {"topic": "GPU costs", "platform": "x", "reason": "trend"})
    assert out["platform"] == "x"
    post = db.add.call_args.args[0]
    assert post.status == "draft"
    assert post.team_id == team
    assert post.user_id == user_id
    assert post.meta_data["action_type"] == "content_topic"
    assert post.meta_data["topic"] == "GPU costs"


@pytest.mark.asyncio
async def test_content_topic_dedup_skip():
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(1))
    out = await DX._exec_content_topic(db, uuid.uuid4(), {"topic": "AI"})
    assert out == {"skipped": "already drafted <24h"}
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_content_topic_defaults(monkeypatch):
    team = uuid.uuid4()
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])
    out = await DX._exec_content_topic(db, team, {"topic": "T"})
    assert out["platform"] == "linkedin"
    post = db.add.call_args.args[0]
    assert post.user_id is None


@pytest.mark.asyncio
async def test_audit_escalation_pings_telegram(monkeypatch):
    team = uuid.uuid4()
    db = _db()
    acc = MagicMock()
    acc.meta_data = {"bot_token_enc": "enc"}
    acc.access_token_enc = b"raw"
    db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(acc)])
    monkeypatch.setattr(DX, "decrypt_token", lambda tok: "decrypted")
    sent = AsyncMock()
    monkeypatch.setattr(DX, "TelegramAPIClient", lambda tok: MagicMock(send_message=sent))
    out = await DX._exec_audit_escalation(db, team, {"emails": ["b@x.gr", "a@x.gr"]})
    assert out == {"pinged": 2}
    sent.assert_awaited_once()
    args = sent.await_args.args
    assert args[0] == DX.TELEGRAM_CHAT
    assert "a@x.gr" in args[1] and "b@x.gr" in args[1]


@pytest.mark.asyncio
async def test_audit_escalation_dedup_and_token_fallback(monkeypatch):
    team = uuid.uuid4()
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(1))
    out = await DX._exec_audit_escalation(db, team, {"emails": ["a@x.gr"]})
    assert out == {"skipped": "already pinged <24h"}

    # no bot_token_enc -> falls back to access_token_enc
    acc = MagicMock()
    acc.meta_data = {}
    acc.access_token_enc = b"raw"
    db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(acc)])
    monkeypatch.setattr(DX, "decrypt_token", lambda tok: "decrypted")
    sent = AsyncMock()
    monkeypatch.setattr(DX, "TelegramAPIClient", lambda tok: MagicMock(send_message=sent))
    out = await DX._exec_audit_escalation(db, team, {"emails": ["a@x.gr"]})
    assert out == {"pinged": 1}
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_devapp_health_alerts_and_dedups(monkeypatch):
    import app.services.slack_notifications as sn

    team = uuid.uuid4()
    db = _db()
    db.execute = AsyncMock(side_effect=[_scalar_result(None)])
    alert = AsyncMock()
    monkeypatch.setattr(sn, "post_alert_to_slack", alert)
    action = {"platform": "x", "username": "tb", "reason": "quota exhausted"}
    out = await DX._exec_devapp_health(db, team, action)
    assert out == {"alerted": "x/@tb"}
    alert.assert_awaited_once()
    assert "quota exhausted" in alert.await_args.args[0]

    # dedup hit
    db.execute = AsyncMock(return_value=_scalar_result(1))
    out = await DX._exec_devapp_health(db, team, action)
    assert out == {"skipped": "already alerted <24h"}


def test_platform_focus():
    assert DX._exec_platform_focus({"platform": "linkedin"}) == {"platform_focus": "linkedin", "weight": "boost"}


# ---------- _run ----------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending",
    [[], [(str(uuid.uuid4()), {"team_id": "x", "actions": []})], [(str(uuid.uuid4()), {})]],
)
async def test_run_skips_without_actions(monkeypatch, pending):
    monkeypatch.setattr(DX, "task_session", lambda: _db())
    monkeypatch.setattr(DX, "_pending_signals", AsyncMock(return_value=pending))
    assert await DX._run() == {"skipped": "no signals"}


@pytest.mark.asyncio
async def test_run_dispatches_actions(monkeypatch):
    db = _db()
    team = uuid.uuid4()
    monkeypatch.setattr(DX, "task_session", lambda: db)
    monkeypatch.setattr(
        DX,
        "_pending_signals",
        AsyncMock(
            return_value=[
                (
                    str(team),
                    {
                        "team_id": str(team),
                        "actions": [
                            {"type": "content_topic", "topic": "AI", "reason": "trend"},
                            {"type": "audit_escalation", "emails": ["b@x.io", "a@x.io"]},
                            {"type": "platform_focus", "platform": "x"},
                            {"type": "unknown"},
                        ],
                    },
                )
            ]
        ),
    )
    topic = AsyncMock(return_value={"drafted": 1})
    monkeypatch.setattr(DX, "_exec_content_topic", topic)
    monkeypatch.setattr(DX, "_exec_audit_escalation", AsyncMock(side_effect=RuntimeError("tg " * 80)))
    monkeypatch.setitem(DX._EXECUTORS, "content_topic", DX._exec_content_topic)
    monkeypatch.setitem(DX._EXECUTORS, "audit_escalation", DX._exec_audit_escalation)

    out = await DX._run()
    assert out["executed"] == 4
    assert out["teams"] == 1
    assert out["results"][0] == {"type": "content_topic", "team": str(team), "drafted": 1}
    assert out["results"][1]["type"] == "audit_escalation" and len(out["results"][1]["error"]) == 120
    assert out["results"][2]["type"] == "platform_focus"
    # unknown action types are recorded as skipped, not silently dropped
    assert out["results"][3] == {"type": "unknown", "team": str(team), "skipped": "no executor"}
    assert topic.await_args.args[1] == team
    events = [c.args[0] for c in db.add.call_args_list]
    assert [e.meta_data["key"] for e in events] == ["AI", "a@x.io,b@x.io", "x", "unknown"]
    assert events[0].meta_data["reason"] == "trend"
    assert all(e.team_id == team for e in events)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_fans_out_across_teams(monkeypatch):
    db = _db()
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(DX, "task_session", lambda: db)
    monkeypatch.setattr(
        DX,
        "_pending_signals",
        AsyncMock(
            return_value=[
                (str(t1), {"actions": [{"type": "platform_focus", "platform": "li"}]}),
                (t2, {"actions": [{"type": "platform_focus", "platform": "x"}]}),  # uuid passthrough
            ]
        ),
    )
    out = await DX._run()
    assert out["executed"] == 2
    assert out["teams"] == 2
    events = [c.args[0] for c in db.add.call_args_list]
    assert {e.team_id for e in events} == {t1, t2}


@pytest.mark.asyncio
async def test_run_records_error_result_for_retry(monkeypatch):
    """Executor exceptions land as result.error — excluded from dedup, so the
    action retries on the next sweep instead of being suppressed for 24h."""
    db = _db()
    team = uuid.uuid4()
    monkeypatch.setattr(DX, "task_session", lambda: db)
    monkeypatch.setattr(
        DX,
        "_pending_signals",
        AsyncMock(return_value=[(str(team), {"actions": [{"type": "devapp_health", "platform": "x", "reason": "quota"}]})]),
    )
    monkeypatch.setitem(DX._EXECUTORS, "devapp_health", AsyncMock(side_effect=RuntimeError("slack down")))
    out = await DX._run()
    assert out["results"][0]["error"] == "slack down"
    ev = db.add.call_args.args[0]
    assert ev.meta_data["result"]["error"] == "slack down"


def test_execute_actions_task(monkeypatch):
    monkeypatch.setattr(DX, "_run", AsyncMock(return_value={"executed": 0}))
    monkeypatch.setattr(DX, "run_async", lambda c: asyncio.run(c))
    assert DX.execute_actions.run() == {"executed": 0}
