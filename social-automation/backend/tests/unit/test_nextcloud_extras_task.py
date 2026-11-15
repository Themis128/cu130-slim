"""Coverage for sync_nextcloud_extras task + _run_extras body."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock


def _run_coro(coro):
    return asyncio.run(coro)


def test_extras_disabled_returns_early(monkeypatch):
    from app.services import nextcloud_sync as svc
    from app.worker.tasks import nextcloud_sync as T

    monkeypatch.setattr(svc, "nextcloud_sync_enabled", lambda: False)
    called = []
    monkeypatch.setattr(T, "run_async", lambda c: called.append(c) or c.close())
    T.sync_nextcloud_extras()
    assert called == []


def test_extras_enabled_runs_sync_extras(monkeypatch):
    from app.services import nextcloud_extras
    from app.services import nextcloud_sync as svc
    from app.worker.tasks import nextcloud_sync as T

    monkeypatch.setattr(svc, "nextcloud_sync_enabled", lambda: True)
    db = MagicMock()

    @asynccontextmanager
    async def _cm():
        yield db

    factory = MagicMock(return_value=_cm())
    monkeypatch.setattr(T, "get_session_factory", lambda: factory)
    seen = {}

    async def _sync_extras(session):
        seen["db"] = session

    monkeypatch.setattr(nextcloud_extras, "sync_extras", _sync_extras)
    monkeypatch.setattr(T, "run_async", _run_coro)
    T.sync_nextcloud_extras()
    assert seen["db"] is db


def test_extras_failure_is_swallowed(monkeypatch):
    from app.services import nextcloud_sync as svc
    from app.worker.tasks import nextcloud_sync as T

    monkeypatch.setattr(svc, "nextcloud_sync_enabled", lambda: True)

    def _boom(coro):
        coro.close()
        raise RuntimeError("extras down")

    monkeypatch.setattr(T, "run_async", _boom)
    T.sync_nextcloud_extras()  # must not raise
