"""datalake_rewards: action scoring, weights, pick_actions gating, task wrapper."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import datalake_rewards as DR
from app.services.datalake_insights import pick_actions
from app.worker.tasks import datalake_rewards as DT


def _ev(meta, ev_id=None):
    ev = MagicMock()
    ev.id = ev_id or uuid.uuid4()
    ev.meta_data = meta
    return ev


def _db_execute_seq(*results):
    """db.execute returns successive results; each result needs the right
    accessor for the call site (scalars().all() / scalar_one_or_none() /
    mappings().all())."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    it = iter(results)

    async def _exec(*_a, **_k):
        return next(it)

    db.execute = AsyncMock(side_effect=_exec)
    return db


def _scalars(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _scalar(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _mappings(rows):
    r = MagicMock()
    r.mappings.return_value.all.return_value = rows
    return r


# ---------- score_actions ----------


@pytest.mark.asyncio
async def test_score_actions_scores_content_topic(monkeypatch):
    team = uuid.uuid4()
    post_id = str(uuid.uuid4())
    action_ev = _ev(
        {"action_type": "content_topic", "key": "AI", "result": {"post_id": post_id}},
    )
    snap = MagicMock(engagement_rate=0.042, impressions=1200)
    db = _db_execute_seq(
        _scalars([action_ev]),  # _action_events
        _scalar(None),  # _already_scored -> False
        _scalar(snap),  # _latest_snapshot
    )
    out = await DR.score_actions(db, team)
    assert out == {"scored": 1, "pending": 0, "skipped": 0}
    ev = db.add.call_args.args[0]
    assert ev.meta_data["action_type"] == "content_topic"
    assert ev.meta_data["reward"] == 0.042
    assert ev.meta_data["action_event_id"] == str(action_ev.id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_score_actions_skips_unmeasurable_and_scored():
    team = uuid.uuid4()
    no_post = _ev({"action_type": "devapp_health", "result": {"alerted": "x"}})
    err = _ev({"action_type": "content_topic", "result": {"error": "boom"}})
    already = _ev({"action_type": "content_topic", "result": {"post_id": str(uuid.uuid4())}})
    db = _db_execute_seq(
        _scalars([no_post, err, already]),
        _scalar(1),  # _already_scored -> True
    )
    out = await DR.score_actions(db, team)
    assert out == {"scored": 0, "pending": 0, "skipped": 3}
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_score_actions_pending_without_snapshot():
    team = uuid.uuid4()
    action_ev = _ev({"action_type": "content_topic", "result": {"post_id": str(uuid.uuid4())}})
    db = _db_execute_seq(
        _scalars([action_ev]),
        _scalar(None),
        _scalar(None),  # no snapshot yet
    )
    out = await DR.score_actions(db, team)
    assert out == {"scored": 0, "pending": 1, "skipped": 0}


# ---------- action_weights ----------


@pytest.mark.asyncio
async def test_action_weights_aggregates():
    team = uuid.uuid4()
    db = _db_execute_seq(
        _mappings(
            [
                {"at": "content_topic", "reward": 0.02},
                {"at": "content_topic", "reward": 0.04},
                {"at": "devapp_health", "reward": None},
                {"at": None, "reward": 0.5},
            ]
        )
    )
    w = await DR.action_weights(db, team)
    assert w == {"content_topic": {"avg_reward": 0.03, "n": 2}}


# ---------- weights_gate ----------


def test_weights_gate_thresholds():
    assert DR.weights_gate(None) == {}
    assert DR.weights_gate({}) == {}
    # enough samples + below floor -> gated
    assert DR.weights_gate({"content_topic": {"avg_reward": 0.001, "n": 5}}) == {"content_topic": True}
    # not enough samples -> allowed
    assert DR.weights_gate({"content_topic": {"avg_reward": 0.0, "n": 2}}) == {}
    # above floor -> allowed
    assert DR.weights_gate({"content_topic": {"avg_reward": 0.02, "n": 9}}) == {}
    # other types never gated
    assert DR.weights_gate({"audit_escalation": {"avg_reward": 0.0, "n": 99}}) == {}


# ---------- pick_actions integration ----------


def test_pick_actions_gates_underperforming_content_topic():
    signals = {
        "pain_topics": [("cloud costs", 5), ("downtime", 3)],
        "action_weights": {"content_topic": {"avg_reward": 0.0, "n": 6}},
    }
    types = [a["type"] for a in pick_actions(signals)]
    assert "content_topic" not in types


def test_pick_actions_allows_without_weights():
    signals = {"pain_topics": [("cloud costs", 5)]}
    types = [a["type"] for a in pick_actions(signals)]
    assert "content_topic" in types


# ---------- task wrapper ----------


def _db_ctx():
    db = MagicMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    teams_res = MagicMock()
    teams_res.scalars.return_value.all.return_value = [uuid.uuid4()]
    db.execute = AsyncMock(return_value=teams_res)
    return db


def test_reward_sweep_task(monkeypatch):
    db = _db_ctx()
    monkeypatch.setattr(DT, "task_session", lambda: db)
    monkeypatch.setattr(DR, "score_actions", AsyncMock(return_value={"scored": 2, "pending": 1, "skipped": 3}))
    monkeypatch.setattr(DR, "action_weights", AsyncMock(return_value={"content_topic": {"avg_reward": 0.03, "n": 4}}))
    monkeypatch.setattr(DT, "run_async", lambda c: asyncio.run(c))
    out = DT.datalake_reward_sweep.run()
    assert out["teams"] == 1
    assert out["details"][0]["scored"] == 2
    assert out["details"][0]["weighted_types"] == 1
