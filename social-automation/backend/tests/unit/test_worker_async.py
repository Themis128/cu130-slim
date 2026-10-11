"""Unit tests for app/worker/_async.py — persistent worker event loop."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

import app.worker._async as W


@pytest.fixture(autouse=True)
def _clean_state():
    yield
    W.stop_worker_loop()
    W._engine = None
    W._session_factory = None


def test_start_get_stop_loop():
    W.start_worker_loop()
    loop = W.get_loop()
    assert loop.is_running()
    # idempotent — same loop on second start
    W.start_worker_loop()
    assert W.get_loop() is loop
    W.stop_worker_loop()
    assert W._loop is None and W._thread is None


def test_run_async_executes_on_worker_loop():
    async def _work():
        return asyncio.get_running_loop()

    loop = W.get_loop()
    assert W.run_async(_work()) is loop


def test_run_async_inside_loop_raises():
    async def _bad():
        return W.run_async(_noop())

    async def _noop():
        return None

    # submitted coroutine runs ON the persistent loop → run_async detects it
    loop = W.get_loop()
    fut = asyncio.run_coroutine_threadsafe(_bad(), loop)
    with pytest.raises(RuntimeError, match="persistent loop"):
        fut.result(10)


def test_run_async_exception_propagates_and_cancels():
    async def _fail():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        W.run_async(_fail())


def test_stop_with_engine_dispose(monkeypatch):
    W.start_worker_loop()

    async def _noop():
        return None

    engine = Mock()
    engine.dispose = Mock(return_value=_noop())
    W._engine = engine
    W.stop_worker_loop()
    engine.dispose.assert_called_once()


def test_stop_dispose_error_swallowed(monkeypatch):
    W.start_worker_loop()

    async def _bad_dispose():
        raise RuntimeError("x")

    engine = Mock()
    engine.dispose = Mock(return_value=_bad_dispose())
    W._engine = engine
    W.stop_worker_loop()  # no raise


def test_stop_when_never_started():
    W.stop_worker_loop()  # _loop/_thread None → no-ops


def test_get_session_factory_lazy(monkeypatch):
    import sqlalchemy.ext.asyncio as sa_async

    engine = Mock()
    monkeypatch.setattr(sa_async, "create_async_engine", lambda *a, **kw: engine)
    f = W.get_session_factory()
    assert f is W.get_session_factory()  # cached
    assert W._engine is engine


@pytest.mark.asyncio
async def test_task_session_yields(monkeypatch):
    session = Mock()

    class _Maker:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    W._session_factory = _Maker()
    async with W.task_session() as s:
        assert s is session
