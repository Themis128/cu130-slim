"""Unit tests for the devapp-channel-actions report→publish hop."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

import app.worker.tasks.devapp_channel_actions as t


def _payload(platform: str = "linkedin", **kw: Any) -> dict:
    base = {
        "platform": platform,
        "account_id": "a1",
        "username": "cloudless-gr",
        "account_type": "organization",
        "ok": True,
        "health": {"token_valid": True},
        "insights": {"profile": {"followers_count": 100}},
        "captured_at": "2026-11-08T00:00:00+00:00",
    }
    base.update(kw)
    return base


class _Mappings:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.committed = False

    async def execute(self, q, params=None):
        return _Mappings(self._rows)

    async def get(self, model, pk):
        return object()  # any truthy team stand-in

    async def commit(self):
        self.committed = True


@asynccontextmanager
async def _session_factory(db):
    yield db


@pytest.mark.asyncio
async def test_run_groups_by_team_and_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    team_id = uuid.uuid4()
    rows = [
        {"team_id": team_id, "meta_data": _payload("linkedin")},
        {"team_id": team_id, "meta_data": _payload("bluesky", ok=False, error="429")},
    ]
    db = _FakeDB(rows)
    monkeypatch.setattr(t, "task_session", lambda: _session_factory(db))

    calls: list[dict] = []

    async def _fake_schedule(db_, team, actions, **kw):
        calls.append({"n_actions": len(actions), **kw})
        return [{"platform": a.platform, "status": "scheduled"} for a in actions]

    monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", _fake_schedule)

    out = await t._run()
    assert out["teams"] == 1
    assert len(calls) == 1
    # healthy linkedin → 1 action; bluesky health override → excluded
    assert calls[0]["n_actions"] == 1
    assert calls[0]["dedup_field"] == "devapp_channel_action"
    assert calls[0]["source"] == "devapp-channel-actions"
    assert db.committed


@pytest.mark.asyncio
async def test_run_skips_team_without_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    team_id = uuid.uuid4()
    rows = [{"team_id": team_id, "meta_data": _payload("bluesky", ok=False, error="dead")}]
    db = _FakeDB(rows)
    monkeypatch.setattr(t, "task_session", lambda: _session_factory(db))

    called = False

    async def _fake_schedule(*a, **kw):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", _fake_schedule)
    out = await t._run()
    assert out["teams"] == 1
    assert called is False  # health-only team never reaches the executor
    assert db.committed


@pytest.mark.asyncio
async def test_run_executor_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    team_id = uuid.uuid4()
    rows = [{"team_id": team_id, "meta_data": _payload("linkedin")}]
    db = _FakeDB(rows)
    monkeypatch.setattr(t, "task_session", lambda: _session_factory(db))

    async def _boom(*a, **kw):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", _boom)
    out = await t._run()
    assert out["results"][0]["status"] == "failed"
    assert "executor exploded" in out["results"][0]["reason"]
    assert db.committed
