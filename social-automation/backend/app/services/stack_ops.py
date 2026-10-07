"""stack-ops integration — idle-sleep/wake-on-connect awareness.

The stack-ops container (see docker-compose `stack-ops` service) fronts
idle-tolerant services with a TCP wake-proxy and stops them after an idle
timeout. This module lets health/status surfaces distinguish "sleeping"
(healthy, will wake on demand) from genuinely down, without touching the
proxied port (a probe through the proxy would wake the service).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

STACK_OPS_URL = os.environ.get("STACK_OPS_URL", "http://stack-ops:8787")
_CACHE_TTL_S = 15.0
_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})


async def statuses() -> dict[str, dict[str, Any]]:
    """All managed services -> {state, last_active_ago_s, active_conns}.

    Fail-open: returns {} when stack-ops is unreachable so callers keep
    working on stacks without it.
    """
    global _cache
    ts, cached = _cache
    if time.monotonic() - ts < _CACHE_TTL_S:
        return cached
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{STACK_OPS_URL.rstrip('/')}/status")
            if resp.status_code == 200:
                data = resp.json()
                _cache = (time.monotonic(), data)
                return data
    except Exception as exc:  # stack-ops down — degrade silently
        logger.debug("stack-ops status unavailable: %s", exc)
    return cached


async def service_state(container_name: str) -> str | None:
    """running | starting | stopped | missing | None (stack-ops absent)."""
    data = await statuses()
    if not data:
        return None
    return data.get(container_name, {}).get("state")


async def is_asleep(container_name: str) -> bool:
    return await service_state(container_name) == "stopped"


async def wake(container_name: str) -> bool:
    """Explicit wake for callers that don't go through the TCP proxy."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{STACK_OPS_URL.rstrip('/')}/wake/{container_name}"
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("stack-ops wake %s failed: %s", container_name, exc)
        return False
