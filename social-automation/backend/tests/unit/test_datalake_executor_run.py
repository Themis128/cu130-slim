"""datalake_executor: action dispatch loop + task wrapper."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.tasks import datalake_executor as DX


def _db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


def test_platform_focus():
    assert DX._exec_platform_focus({"platform": "linkedin"}) == {"platform_focus": "linkedin", "weight": "boost"}


@pytest.mark.asyncio
@pytest.mark.parametrize("signals", [{}, {"team_id": str(uuid.uuid4()), "actions": []}])
async def test_run_skips_without_signals(monkeypatch, signals):
    monkeypatch.setattr(DX, "task_session", lambda: _db())
    monkeypatch.setattr(DX, "_latest_signals", AsyncMock(return_value=signals))
    assert await DX._run() == {"skipped": "no signals"}


@pytest.mark.asyncio
async def test_run_dispatches_actions(monkeypatch):
    db = _db()
    team = uuid.uuid4()
    monkeypatch.setattr(DX, "task_session", lambda: db)
    monkeypatch.setattr(
        DX,
        "_latest_signals",
        AsyncMock(
            return_value={
                "team_id": str(team),
                "actions": [
                    {"type": "content_topic", "topic": "AI", "reason": "trend"},
                    {"type": "audit_escalation"},
                    {"type": "platform_focus", "platform": "x"},
                    {"type": "unknown"},
                ],
            }
        ),
    )
    topic = AsyncMock(return_value={"drafted": 1})
    monkeypatch.setattr(DX, "_exec_content_topic", topic)
    monkeypatch.setattr(DX, "_exec_audit_escalation", AsyncMock(side_effect=RuntimeError("tg " * 80)))
    monkeypatch.setitem(DX._EXECUTORS, "content_topic", DX._exec_content_topic)
    monkeypatch.setitem(DX._EXECUTORS, "audit_escalation", DX._exec_audit_escalation)

    out = await DX._run()
    assert out["executed"] == 3
    assert out["results"][0] == {"type": "content_topic", "drafted": 1}
    assert out["results"][1]["type"] == "audit_escalation" and len(out["results"][1]["error"]) == 120
    assert out["results"][2] == {"type": "platform_focus", "platform_focus": "x", "weight": "boost"}
    assert topic.await_args.args[1] == team
    events = [c.args[0] for c in db.add.call_args_list]
    assert [e.meta_data["key"] for e in events] == ["AI", "audit_escalation", "x"]
    assert events[0].meta_data["reason"] == "trend"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_accepts_uuid_team(monkeypatch):
    team = uuid.uuid4()
    monkeypatch.setattr(DX, "task_session", lambda: _db())
    monkeypatch.setattr(DX, "_latest_signals", AsyncMock(return_value={"team_id": team, "actions": [{"type": "platform_focus", "platform": "li"}]}))
    out = await DX._run()
    assert out["executed"] == 1


def test_execute_actions_task(monkeypatch):
    monkeypatch.setattr(DX, "_run", AsyncMock(return_value={"executed": 0}))
    monkeypatch.setattr(DX, "run_async", lambda c: asyncio.run(c))
    assert DX.execute_actions.run() == {"executed": 0}
