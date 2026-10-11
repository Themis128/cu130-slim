"""Unit tests for app/worker/tasks/linkedin_invites.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_invites as LW


def _db(*results):
    """Queue results: teams, then per-team (acct, dup) pairs."""
    queue = list(results)

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return queue.pop(0)

    return _DB


def _res(*, scalars=None, scalar=None, first=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: scalars or []),
        scalar_one_or_none=lambda: scalar,
        first=lambda: first)


@pytest.mark.asyncio
async def test_log_invite_event_gates(monkeypatch):
    recorded = AsyncMock()
    import app.services.growth_initiatives as GI
    monkeypatch.setattr(GI, "record_initiative_event", recorded)

    # neither sent nor no_credits → early return, no db
    await LW._log_invite_event({"status": "error", "sent": 0})
    recorded.assert_not_awaited()

    # no_credits → units=0, credits_left=0 recorded
    team = SimpleNamespace(id=uuid.uuid4())
    acct_id = uuid.uuid4()
    monkeypatch.setattr(LW, "_worker_db", lambda: _db(
        _res(scalars=[team]),          # teams
        _res(scalar=acct_id),           # acct lookup
        _res(first=None),               # no dup
    )())
    await LW._log_invite_event({"status": "failed", "sent": 0,
                                "reason": "no_credits"})
    recorded.assert_awaited_once()
    kw = recorded.await_args.kwargs
    assert kw["units"] == 0 and kw["credits_left"] == 0
    assert kw["note"] == "auto daily batch"
    assert kw["account_id"] == acct_id


@pytest.mark.asyncio
async def test_log_invite_event_sent_and_dup(monkeypatch):
    recorded = AsyncMock()
    import app.services.growth_initiatives as GI
    monkeypatch.setattr(GI, "record_initiative_event", recorded)
    team = SimpleNamespace(id=uuid.uuid4())

    # sent → credits_left = available - sent
    monkeypatch.setattr(LW, "_worker_db", lambda: _db(
        _res(scalars=[team]), _res(scalar=uuid.uuid4()),
        _res(first=None))())
    await LW._log_invite_event({"status": "sent", "sent": 30,
                                "credits_available": 100})
    assert recorded.await_args.kwargs["units"] == 30
    assert recorded.await_args.kwargs["credits_left"] == 70

    # no acct → skipped
    recorded.reset_mock()
    monkeypatch.setattr(LW, "_worker_db", lambda: _db(
        _res(scalars=[team]), _res(scalar=None))())
    await LW._log_invite_event({"status": "sent", "sent": 30})
    recorded.assert_not_awaited()

    # dup event in last 12h → skipped
    monkeypatch.setattr(LW, "_worker_db", lambda: _db(
        _res(scalars=[team]), _res(scalar=uuid.uuid4()),
        _res(first=("dup-id",)))())
    await LW._log_invite_event({"status": "sent", "sent": 30})
    recorded.assert_not_awaited()


def test_send_linkedin_invites_task(monkeypatch):
    import app.services.linkedin_invites as SI
    batch = AsyncMock(return_value={"status": "sent", "sent": 5})
    monkeypatch.setattr(SI, "send_invite_batch", batch)

    run = []
    def fake_ra(coro):
        run.append(coro)
        coro.close()
        return {"status": "sent", "sent": 5}

    monkeypatch.setattr(LW, "_run_async", fake_ra)
    out = LW.send_linkedin_invites(batch_size=10)
    assert out == {"status": "sent", "sent": 5}
    batch.assert_called_once_with(10)  # coro created, closed un-awaited
    assert len(run) == 2  # batch + log

    # log failure tolerated
    calls = iter([{"status": "sent"}, RuntimeError("log boom")])

    def fake_ra2(coro):
        v = next(calls)
        coro.close()
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(LW, "_run_async", fake_ra2)
    out = LW.send_linkedin_invites(batch_size=10)
    assert out == {"status": "sent"}  # log error swallowed
