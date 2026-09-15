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

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_STATUS_KEY = "dmr:status"
_STATUS_TTL = 900  # 15 min — 3x the check interval


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (unlikely for celery) — run in a new thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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
