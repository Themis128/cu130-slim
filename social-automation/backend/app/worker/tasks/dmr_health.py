"""Periodic DMR (Docker Model Runner) health monitoring.

Runs every 5 minutes via celery-beat on the default queue. Probes the GPU
runner (host port 12435), validates that expected models are present in the
store, and publishes the result to Redis (`dmr:status`) so the API/UI can
surface runner health without hitting the runner on every request.

On failure the inference circuit breaker already routes to Cloudflare Workers
AI automatically; this task exists for visibility and faster detection. The
companion `dmr-watchdog` compose container handles runner restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_STATUS_KEY = "dmr:status"
_STATUS_TTL = 900  # 15 min — 3x the check interval


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run_async(coro)
    # Already inside a loop (unlikely for celery) — run in a new thread
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run_async, coro).result()


async def _check() -> dict:
    from app.services import dmr

    started = time.perf_counter()
    online = await dmr._check_dmr_health()
    validation = {}
    if online:
        try:
            validation = await dmr.validate_dmr_models()
        except Exception as exc:
            logger.warning("DMR model validation error: %s", exc)
    status = {
        "online": online,
        "missing_models": validation.get("missing", []),
        "checked_at": time.time(),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
    try:
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        r = aioredis.from_url(get_settings().REDIS_URL)
        await r.setex(_STATUS_KEY, _STATUS_TTL, json.dumps(status))
        await r.aclose()
    except Exception as exc:
        logger.debug("DMR status publish to Redis failed: %s", exc)

    if not online:
        logger.warning(
            "DMR health check FAILED — inference is falling back to Cloudflare Workers AI. "
            "Check: docker ps | grep docker-model-runner; docker logs docker-model-runner"
        )
    elif status["missing_models"]:
        logger.warning("DMR online but missing models: %s", status["missing_models"])
    return status


@celery_app.task(name="app.worker.tasks.dmr_health.check_dmr_health")
def check_dmr_health() -> dict:
    """Probe DMR health + model store; publish to Redis `dmr:status`."""
    return _run_async(_check())


async def _keep_warm() -> dict:
    from app.services import dmr

    return await dmr.keep_warm_models()


_KEEP_WARM_LOCK = "dmr:keepwarm:lock"
_KEEP_WARM_LOCK_TTL = 900  # covers even the slowest cold-start run
_KEEP_WARM_LAST = "dmr:keepwarm:last_run"
_KEEP_WARM_MIN_GAP = 60.0  # fresh completions satisfy the 90s cadence


def _keep_warm_lock_client():
    """Sync Redis client for the run-in-progress lock. Fail-open."""
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        return None


@celery_app.task(name="app.worker.tasks.dmr_health.keep_warm_dmr_models")
def keep_warm_dmr_models() -> dict:
    """Refresh keep_alive on the warm-tier model (0.6B embedding only).

    Runs every 90s via celery-beat — below the embedding model's 15m
    keep_alive — so recall/remember calls never pay a cold-load. The
    mid-4B + llama3.2 pinning was removed 2026-10-28 (VRAM saturation →
    daemon instability); they now ride their own configured keep_alive.
    Skips while a media job owns the GPU. A run-in-progress lock collapses
    beat pile-up: cold-start runs take minutes, so queued duplicates would
    otherwise hog both media-worker slots and starve video renders.
    """
    r = _keep_warm_lock_client()
    token = str(time.time_ns())
    locked = False
    if r is not None:
        try:
            last = r.get(_KEEP_WARM_LAST)
            last_ts = last.decode() if isinstance(last, bytes) else (str(last) if last is not None else None)
            if last_ts and time.time() - float(last_ts) < _KEEP_WARM_MIN_GAP:
                return {"warmed": [], "skipped": [], "reason": "recent-run"}
            locked = bool(r.set(_KEEP_WARM_LOCK, token, nx=True, ex=_KEEP_WARM_LOCK_TTL))
            if not locked:
                return {"warmed": [], "skipped": [], "reason": "run-in-progress"}
        except Exception:  # noqa: BLE001
            logger.warning("keep-warm lock check failed — proceeding", exc_info=True)
            r = None
    try:
        return _run_async(_keep_warm())
    finally:
        if locked and r is not None:
            try:
                if r.get(_KEEP_WARM_LOCK) == token:
                    r.delete(_KEEP_WARM_LOCK)
                r.set(_KEEP_WARM_LAST, str(time.time()), ex=_KEEP_WARM_LOCK_TTL)
            except Exception:  # noqa: BLE001
                logger.debug("keep-warm lock release failed", exc_info=True)
