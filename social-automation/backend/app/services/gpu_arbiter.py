"""GPU arbitration for the single 8GB card.

Owner directive (2026-10-04): only ONE model family may hold VRAM at a
time. Media jobs (ComfyUI image/video) must own the GPU exclusively —
the 2026-10-04 contention incident (ComfyUI + two DMR llama-servers
resident, 329MB free of 8GB) produced the distorted images that shipped
in that day's digest.

Design:
  - Media tasks wrap their ComfyUI call in ``media_gpu_lock()``, which
    takes a Redis lock (crash-safe via TTL), sets a fast busy flag, and
    unloads every resident DMR model through the HTTP API before the
    render starts. Released in ``finally``.
  - DMR entry points call ``await_media_idle()`` first: while the flag
    is set they poll-wait instead of loading a model into a full card.
    The wait is bounded — if a lock holder wedges, DMR degrades to
    competing rather than blocking forever.

The host-side manual equivalent is ``scripts/gpu_serial.py``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_LOCK_KEY = "gpu:media_lock"
_BUSY_KEY = "gpu:media_busy"
# Safety TTL: a wedged holder self-releases. Long video jobs refresh it.
_LOCK_TTL_S = 1800
_LOCK_POLL_S = 2.0
# How long a DMR caller waits for a media job to finish before competing
# anyway (keeps text inference bounded even if the flag leaks).
_DMR_WAIT_MAX_S = 300.0

# Cluster-wide single-flight FIFO for DMR requests.
_DMR_LOCK_KEY = "gpu:dmr_lock"
# One DMR request never legitimately holds the slot longer than this;
# a crashed holder self-releases via TTL instead of deadlocking inference.
_DMR_LOCK_TTL_S = 300
# Bounded acquire — a wedged holder degrades callers to competing rather
# than blocking forever (same philosophy as _DMR_WAIT_MAX_S).
_DMR_LOCK_ACQUIRE_S = 240.0


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


async def media_gpu_busy() -> bool:
    """True while a media job holds the GPU."""
    try:
        r = await _redis()
        try:
            return bool(await r.exists(_BUSY_KEY))
        finally:
            await r.aclose()
    except Exception:
        return False  # Redis down — can't arbitrate, proceed


async def await_media_idle(max_wait_s: float = _DMR_WAIT_MAX_S) -> None:
    """Block while a media job owns the GPU (bounded, best-effort)."""
    waited = 0.0
    while waited < max_wait_s and await media_gpu_busy():
        await asyncio.sleep(_LOCK_POLL_S)
        waited += _LOCK_POLL_S
    if waited:
        logger.info("[gpu-arbiter] waited %.0fs for media job to release GPU", waited)


@asynccontextmanager
async def media_gpu_lock(
    acquire_timeout_s: float = 600.0,
    *,
    ttl_s: int = _LOCK_TTL_S,
) -> AsyncIterator[None]:
    """Exclusive GPU access for one media job.

    Acquires the Redis lock, unloads resident DMR models, then yields.
    Non-holder DMR requests see ``media_gpu_busy()`` for the duration.
    If Redis is unreachable the lock degrades to a no-op with a warning —
    media generation must not depend on Redis being up.
    """
    token = uuid.uuid4().hex
    acquired = False
    r = None
    try:
        r = await _redis()
    except Exception as exc:
        logger.warning("[gpu-arbiter] Redis unreachable (%s) — running unserialized", type(exc).__name__)
        yield
        return

    try:
        waited = 0.0
        while waited < acquire_timeout_s:
            if await r.set(_LOCK_KEY, token, nx=True, ex=ttl_s):
                acquired = True
                break
            await asyncio.sleep(_LOCK_POLL_S)
            waited += _LOCK_POLL_S
        if not acquired:
            raise TimeoutError(
                f"gpu media lock not acquired within {acquire_timeout_s:.0f}s"
            )
        await r.set(_BUSY_KEY, "1", ex=ttl_s)
        # Evict resident DMR models so the renderer sees a free card.
        # Imported here to avoid a module cycle (dmr imports this module
        # for the busy wait).
        from app.services.dmr import _unload_idle_models

        await _unload_idle_models()
        logger.info("[gpu-arbiter] GPU lock acquired; DMR models unloaded")
        try:
            yield
        finally:
            # Return the card to DMR: ComfyUI caches loaded checkpoints with
            # no TTL, so without this the next llama-server load hangs behind
            # a full GPU until its client timeout (the 2026-10-05
            # generate-content wedge).
            try:
                import httpx

                base = get_settings().COMFYUI_URL.rstrip("/")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    q = await client.get(f"{base}/queue", timeout=3.0)
                    running = (
                        len((q.json() if q.status_code == 200 else {}).get("queue_running") or []) > 0
                    )
                    if not running:
                        await client.post(
                            f"{base}/free",
                            json={"unload_models": True, "free_memory": True},
                        )
            except Exception as exc:
                logger.debug("[gpu-arbiter] post-job ComfyUI free failed (%s)", type(exc).__name__)
    finally:
        if r is not None:
            try:
                # Delete only if we still own the lock (TTL may have
                # expired and another job taken it).
                cur = await r.get(_LOCK_KEY)
                if acquired and cur == token:
                    await r.delete(_LOCK_KEY)
                await r.delete(_BUSY_KEY)
            finally:
                await r.aclose()
