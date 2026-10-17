"""Branch tests for the worker ``_run_async`` shims and the
``get_session_factory`` double-checked lock.

Each messenger/session task carries a local ``_run_async`` that offloads
to a thread when a loop is already running, and otherwise defers to the
shared ``app.worker._async.run_async``. The running-loop path is covered
elsewhere; these tests pin the idle-loop fallthrough.
"""
from __future__ import annotations

import importlib

import pytest

import app.worker._async as A

MODULES = [
    "app.worker.tasks.instagram_messenger",
    "app.worker.tasks.linkedin_invites",
    "app.worker.tasks.linkedin_session_refresh",
    "app.worker.tasks.threads_messenger",
    "app.worker.tasks.tiktok_messenger",
    "app.worker.tasks.twitter_messenger",
]


class _IdleLoop:
    def is_running(self) -> bool:
        return False


@pytest.mark.parametrize("modname", MODULES)
def test_idle_loop_defers_to_shared_runner(monkeypatch, modname):
    mod = importlib.import_module(modname)
    seen = []

    def _fake_run_async(coro):
        seen.append(coro)
        coro.close()  # never awaited; avoid the RuntimeWarning
        return "ran"

    monkeypatch.setattr(mod.asyncio, "get_event_loop", lambda: _IdleLoop())
    monkeypatch.setattr(mod, "run_async", _fake_run_async)

    async def _job():
        return 1

    assert mod._run_async(_job()) == "ran"
    assert len(seen) == 1


def test_session_factory_created_by_racing_thread(monkeypatch):
    """Another thread wins the race between the unlocked check and the
    lock: the inner re-check must reuse its factory, not build a second
    engine."""
    sentinel = object()

    class _RacingLock:
        def __enter__(self):
            A._session_factory = sentinel  # the other thread finished first
            return self

        def __exit__(self, *exc):
            return False

    def _boom(*a, **kw):
        raise AssertionError("engine must not be recreated")

    monkeypatch.setattr(A, "_session_factory", None)
    monkeypatch.setattr(A, "_lock", _RacingLock())
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", _boom)
    assert A.get_session_factory() is sentinel
