"""Persistent asyncio loop for Celery prefork workers.

Celery's prefork pool has no event loop. The common workaround —
``asyncio.run()`` per task — creates and destroys a loop every invocation,
which is why ``async_session_maker``'s pooled connections crash with
"Future attached to a different loop" the moment a second task reuses a
connection bound to a dead loop (observed 2026-10-08 in skool_watch).
Per-task ``NullPool`` engines avoid the crash but pay a full reconnect on
every task and destroy every pooled resource per invocation.

This module instead runs ONE event loop in a daemon thread per worker
child, started by ``worker_process_init`` (after the fork — a loop or
engine created in the parent would carry the same cross-process bug).
Tasks submit coroutines via ``run_coroutine_threadsafe``. The async
engine is created lazily inside that loop, so pooled connections live as
long as the child process and real connection pooling works again.

Usage in a task body::

    from app.worker._async import run_async, task_session

    async def _work():
        async with task_session() as db:
            ...

    @shared_task
    def my_task():
        return run_async(_work())
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_engine = None
_session_factory = None
_lock = threading.Lock()


def start_worker_loop() -> None:
    """Start the per-process loop thread. Idempotent.

    Called from ``worker_process_init`` so the loop always exists in the
    prefork child — never inherited across the fork. Also self-heals if
    invoked without the signal (e.g. eager/solo pools, tests).
    """
    global _loop, _thread
    with _lock:
        if _loop is not None and _loop.is_running():
            return
        loop = asyncio.new_event_loop()
        _loop = loop

        def _run() -> None:
            # set_event_loop so legacy library calls to asyncio.get_event_loop()
            # inside the thread resolve to this loop.
            asyncio.set_event_loop(loop)
            loop.run_forever()

        _thread = threading.Thread(
            target=_run,
            name="celery-asyncio-loop",
            daemon=True,
        )
        _thread.start()


def stop_worker_loop() -> None:
    """Dispose the engine then stop the loop (``worker_process_shutdown``)."""
    global _loop, _thread, _engine, _session_factory
    with _lock:
        loop, engine, thread = _loop, _engine, _thread
        _loop = _engine = _session_factory = _thread = None
    if loop is not None and loop.is_running():
        if engine is not None:
            try:
                asyncio.run_coroutine_threadsafe(engine.dispose(), loop).result(10)
            except Exception as exc:  # noqa: BLE001 — shutdown is best-effort
                logger.debug("engine dispose on shutdown failed: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=5)


def get_loop() -> asyncio.AbstractEventLoop:
    """Return the running persistent loop, starting it if needed."""
    if _loop is None or not _loop.is_running():
        start_worker_loop()
    assert _loop is not None  # noqa: S101 — start_worker_loop guarantees it
    return _loop


def run_async(coro: Coroutine[Any, Any, Any], timeout: float | None = None) -> Any:
    """Submit a coroutine to the worker's persistent loop and wait for it.

    Must be called from the synchronous task body (the prefork child's main
    thread) — never from inside a coroutine already running on the loop,
    where the blocking ``.result()`` would deadlock.
    """
    loop = get_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        raise RuntimeError("run_async() called from the persistent loop thread")
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return fut.result(timeout)
    except BaseException:
        fut.cancel()
        raise


def get_session_factory():
    """Return the per-worker ``async_sessionmaker``, creating the engine lazily.

    The engine object is thread-safe (SQLAlchemy docs: only the pooled
    *connections* are loop-bound, and those are created inside the
    persistent loop on first checkout). Safe to call from the task body
    or inside a coroutine — connections always open on the worker loop.
    """
    global _engine, _session_factory
    if _session_factory is None:
        with _lock:
            if _session_factory is None:
                from sqlalchemy.ext.asyncio import (
                    AsyncSession,
                    async_sessionmaker,
                    create_async_engine,
                )

                from app.core.config import get_settings

                _engine = create_async_engine(
                    get_settings().DATABASE_URL, pool_size=4, max_overflow=4
                )
                _session_factory = async_sessionmaker(
                    _engine, class_=AsyncSession, expire_on_commit=False
                )
    return _session_factory


@asynccontextmanager
async def task_session():
    """AsyncSession on the per-worker engine.

    Lazily builds a real ``QueuePool`` engine on first use — inside the
    persistent loop, so pooled connections stay bound to the loop that
    created them for the whole child-process lifetime.
    """
    async with get_session_factory()() as session:
        yield session
