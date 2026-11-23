"""Watcher gate — central enable/disable for Celery-executed watchers.

``task_prerun`` receiver: when a task's name matches an ``ops_watchers``
row with ``executor="celery"`` and ``enabled=False``, the task is skipped
(``Ignore``) before it runs. Registry rows are cached briefly so the gate
adds no per-task DB round-trip cost under the publish queue's 30s cadence.

Only tasks explicitly registered in ``ops_watchers`` are gated — every
other task passes through untouched.
"""

from __future__ import annotations

import logging
import time

from celery.exceptions import Ignore

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0
_cache: dict[str, bool] = {}  # task_name -> enabled
_cache_at = 0.0


def _load_enabled() -> dict[str, bool]:
    """task_name -> enabled for all celery-executor watcher rows."""
    global _cache_at
    now = time.monotonic()
    if now - _cache_at < _CACHE_TTL and _cache:
        return _cache
    try:
        import asyncio

        from sqlalchemy import select

        from app.db.session import async_session_maker
        from app.models.ops_watcher import OpsWatcher, WatcherExecutor

        async def _q():
            async with async_session_maker() as db:
                rows = (await db.execute(select(OpsWatcher.command, OpsWatcher.enabled).where(OpsWatcher.executor == WatcherExecutor.CELERY))).all()
                return {cmd: en for cmd, en in rows}

        _cache.clear()
        _cache.update(asyncio.run(_q()))
        _cache_at = now
    except Exception as exc:  # registry unreadable → fail-open, never block tasks
        logger.warning("watcher gate registry read failed (fail-open): %s", exc)
        _cache_at = now  # don't hammer the DB every task
    return _cache


def gate(task=None, **_kwargs) -> None:
    """task_prerun receiver — skips disabled registered watchers."""
    if task is None:
        return
    name = getattr(task, "name", None)
    if not name:
        return
    enabled_map = _load_enabled()
    if name in enabled_map and not enabled_map[name]:
        logger.info("watcher gate: skipping disabled task %s", name)
        raise Ignore(f"watcher '{name}' disabled via ops_watchers")


def register(app) -> None:
    from celery.signals import task_prerun

    task_prerun.connect(gate)
