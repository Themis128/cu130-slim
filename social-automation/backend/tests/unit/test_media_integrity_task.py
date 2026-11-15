"""Coverage for the media_integrity Celery task wrapper."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import app.worker.tasks.media_integrity as MI


def _drain(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        s = MagicMock()
        s.all.return_value = self._rows
        return s


def _db(teams):
    db = SimpleNamespace()
    db.execute = AsyncMock(return_value=_Exec(teams))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _session(db):
    @asynccontextmanager
    async def _cm():
        yield db

    return _cm


def _team():
    return SimpleNamespace(id=uuid.uuid4())


def _run_task(teams, sweep_side_effect=None, sync_error=False):
    db = _db(teams)
    sweep = AsyncMock(side_effect=sweep_side_effect)
    if sweep_side_effect is None:
        sweep.return_value = {"checked": 1, "repaired": 0, "missing": 0, "size_mismatch": 0, "backend_errors": 0}
    sync = AsyncMock(side_effect=RuntimeError("sync down") if sync_error else None)
    with (
        patch.object(MI, "run_async", _drain),
        patch.object(MI, "task_session", _session(db)),
        patch.object(MI.media_integrity, "run_integrity_sweep", sweep),
        patch.object(MI, "sync_after_worker_task", sync),
    ):
        return MI.run_media_integrity_sweep(batch_size=50, notify=False), db, sweep


def test_no_teams_returns_zeroed_report():
    out, db, sweep = _run_task([])
    assert out["teams"] == 0 and out["checked"] == 0
    sweep.assert_not_awaited()


def test_sweep_runs_per_team_and_aggregates():
    t1, t2 = _team(), _team()
    out, db, sweep = _run_task([t1, t2])
    assert out["teams"] == 2 and out["checked"] == 2
    assert sweep.await_count == 2
    assert db.commit.await_count == 2
    assert "reports" in out and len(out["reports"]) == 2


def test_team_failure_rolls_back_and_continues():
    t1, t2 = _team(), _team()
    out, db, sweep = _run_task(
        [t1, t2],
        sweep_side_effect=[RuntimeError("storage down"), {"checked": 3, "missing": 1}],
    )
    assert out["teams"] == 2
    assert out["reports"][0]["error"] == "storage down"
    assert out["reports"][1]["checked"] == 3
    assert db.rollback.await_count == 1
    assert db.commit.await_count == 1


def test_sync_failure_is_best_effort():
    out, _, _ = _run_task([_team()], sync_error=True)
    assert out["teams"] == 1


def test_outer_exception_returns_error_dict():
    def _raise(coro, *a, **kw):
        coro.close()
        raise RuntimeError("boom")

    with patch.object(MI, "run_async", _raise):
        out = MI.run_media_integrity_sweep()
    assert out == {"error": "boom"}
