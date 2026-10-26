"""Latency watchdog — self-healing rollback on SLO breach.

Cloudless's SLO: 12ms claimed latency, alert threshold >20ms sustained.
When the edge response exceeds it for N consecutive checks, the
deployment self-heals via `wrangler rollback` on the pi-origin-proxy
worker — the Cloudflare-side kill-switch, not a notification.

Runs on the default queue every 5min via beat.
"""
from __future__ import annotations

import logging
import os
import time

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)

_EDGE_URL = os.environ.get("EDGE_CHECK_URL", "https://cloudless.gr")
_SLO_MS = float(os.environ.get("EDGE_SLO_MS", "20"))
_BREACH_THRESHOLD = int(os.environ.get("EDGE_BREACH_COUNT", "3"))
# Track consecutive breaches in a module-level counter — a single spike
# (cold edge, DNS jitter) shouldn't roll back production.
_breach_count = 0


async def _measure() -> float:
    """Edge round-trip latency in ms."""
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.get(_EDGE_URL, follow_redirects=True)
    return (time.monotonic() - t0) * 1000


async def _rollback() -> bool:
    """wrangler rollback on the pi-origin-proxy — the self-heal path.
    Only fires inside a container that has wrangler + CF credentials."""
    import asyncio
    import shutil
    if not shutil.which("wrangler"):
        logger.warning("wrangler not in this container — rollback is a host op")
        return False
    proc = await asyncio.create_subprocess_exec(
        "wrangler", "rollback", "--name", "cloudless2",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.error("wrangler rollback failed: %s", err.decode()[:300])
        return False
    logger.info("rollback executed — latency SLO breach self-healed")
    return True


@shared_task
def run_latency_watchdog() -> dict:
    import asyncio
    return asyncio.get_event_loop().run_until_complete(_check()) if not asyncio.get_event_loop().is_running() else asyncio.run(_check())


async def _check() -> dict:
    global _breach_count
    ms = await _measure()
    if ms <= _SLO_MS:
        if _breach_count:
            logger.info("edge recovered to %.0fms — breach counter reset", ms)
        _breach_count = 0
        return {"latency_ms": round(ms, 1), "breach": 0, "rolled_back": False}
    _breach_count += 1
    logger.warning("edge latency %.0fms > %.0fms SLO (breach %d/%d)",
                   ms, _SLO_MS, _breach_count, _BREACH_THRESHOLD)
    rolled = False
    if _breach_count >= _BREACH_THRESHOLD:
        rolled = await _rollback()
        if rolled:
            _breach_count = 0
    return {"latency_ms": round(ms, 1), "breach": _breach_count, "rolled_back": rolled}
