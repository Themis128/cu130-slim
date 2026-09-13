"""Browser Bridge Orchestrator.

Coordinates access to the single shared browser-novnc browser session
across multiple Celery workers (Instagram, Threads, Twitter, TikTok,
personal Messenger, LinkedIn).

Problem: The browser bridge (port 9223) has ONE headed Chromium instance.
When worker A navigates to facebook.com and worker B tries to fetch
instagram.com API, worker B gets "Failed to fetch" because the browser
is on a different origin. This causes DM polls to silently fail.

Solution: A Redis-based distributed lock with fair scheduling. Each
worker acquires the lock for its platform, navigates, does its work,
and releases the lock. Other workers wait their turn.

Usage:
    from app.services.browser_orchestrator import browser_session

    async with browser_session("instagram", bridge) as b:
        convos = await b.get_instagram_dm_conversations()
        # ... do work ...
    # Lock auto-released on exit
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Redis keys
_LOCK_KEY = "browser-bridge:lock"           # Global lock (one user at a time)
_QUEUE_KEY = "browser-bridge:queue"          # Fair scheduling queue
_PLATFORM_KEY = "browser-bridge:platform"   # Current platform using the browser

# Timing — keep these balanced to prevent deadlocks but allow enough time
# for browser interactions (navigate + sleep + fetch can take 10-20s,
# and the personal messenger conversation scraping can take 30-40s)
_LOCK_TIMEOUT = 90           # Auto-release after 90s (enough for any browser interaction)
_LOCK_RETRY_DELAY = 0.3      # Time between lock acquisition attempts
_MAX_WAIT = 60               # Max seconds to wait for the lock


async def _get_redis() -> Any:
    """Get a Redis client for the orchestrator."""
    import redis.asyncio as aioredis
    settings = get_settings()
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


class BrowserSession:
    """Context manager for exclusive browser bridge access.

    Acquires a Redis-based lock, tracks the platform, and releases on exit.
    Other workers wait until the lock is released before proceeding.

    Usage:
        async with BrowserSession("instagram", bridge) as b:
            await b.get_instagram_dm_conversations()
    """

    def __init__(
        self,
        platform: str,
        bridge: Any,
        max_wait: float = _MAX_WAIT,
    ) -> None:
        self.platform = platform
        self.bridge = bridge
        self.max_wait = max_wait
        self._lock_token: str | None = None
        self._acquired = False
        self._start_time = 0.0

    async def __aenter__(self) -> Any:
        """Acquire the browser lock. Returns the bridge instance."""
        self._start_time = time.perf_counter()
        try:
            r = await _get_redis()
        except Exception as exc:
            logger.warning(
                "Browser orchestrator: Redis unavailable (%s) — "
                "proceeding without lock (best effort)",
                exc,
            )
            return self.bridge

        self._lock_token = f"{self.platform}:{time.time()}"

        # Fair scheduling: add to queue, wait for our turn
        await r.rpush(_QUEUE_KEY, self._lock_token)

        try:
            deadline = time.perf_counter() + self.max_wait
            while time.perf_counter() < deadline:
                # Check if we're at the front of the queue
                front = await r.lindex(_QUEUE_KEY, 0)
                if front == self._lock_token:
                    # We're next — try to acquire the lock
                    acquired = await r.set(
                        _LOCK_KEY, self._lock_token,
                        nx=True, ex=_LOCK_TIMEOUT,
                    )
                    if acquired:
                        self._acquired = True
                        await r.set(_PLATFORM_KEY, self.platform, ex=_LOCK_TIMEOUT)
                        wait_time = time.perf_counter() - self._start_time
                        if wait_time > 1.0:
                            logger.info(
                                "Browser orchestrator: %s acquired lock "
                                "after %.1fs wait",
                                self.platform, wait_time,
                            )
                        else:
                            logger.debug(
                                "Browser orchestrator: %s acquired lock (%.1fs)",
                                self.platform, wait_time,
                            )
                        return self.bridge

                # Not our turn yet — wait and retry
                await asyncio.sleep(_LOCK_RETRY_DELAY)

            # Timeout — remove from queue and proceed without lock
            wait_time = time.perf_counter() - self._start_time
            logger.warning(
                "Browser orchestrator: %s timed out after %.1fs waiting for lock — "
                "proceeding without lock (best effort)",
                self.platform, wait_time,
            )
            await self._remove_from_queue(r)
            return self.bridge

        except Exception as exc:
            logger.warning(
                "Browser orchestrator: lock acquisition error for %s: %s — "
                "proceeding without lock",
                self.platform, exc,
            )
            try:
                await self._remove_from_queue(r)
            except Exception:
                pass
            return self.bridge

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Release the browser lock."""
        hold_time = time.perf_counter() - self._start_time if self._start_time else 0

        if not self._acquired or self._lock_token is None:
            return

        try:
            r = await _get_redis()
            # Only release if we still own the lock (token matches)
            current = await r.get(_LOCK_KEY)
            if current and str(current) == str(self._lock_token):
                await r.delete(_LOCK_KEY)
                await r.delete(_PLATFORM_KEY)
                logger.debug(
                    "Browser orchestrator: %s released lock (held %.1fs)",
                    self.platform, hold_time,
                )
            # Remove ourselves from the queue
            await self._remove_from_queue(r)
        except Exception as exc:
            logger.debug("Browser orchestrator: lock release failed: %s", exc)

    async def _remove_from_queue(self, r: Any) -> None:
        """Remove our entry from the fair scheduling queue."""
        try:
            if self._lock_token:
                await r.lrem(_QUEUE_KEY, 0, self._lock_token)
        except Exception:
            pass


def browser_session(
    platform: str,
    bridge: Any,
    max_wait: float = _MAX_WAIT,
) -> BrowserSession:
    """Create a browser session context manager.

    Args:
        platform: The platform name (instagram, threads, twitter, tiktok, facebook, linkedin)
        bridge: The BrowserBridgeClient instance
        max_wait: Maximum seconds to wait for the lock (default 30s)

    Returns:
        A context manager that provides exclusive browser bridge access.

    Usage:
        async with browser_session("instagram", bridge) as b:
            convos = await b.get_instagram_dm_conversations()
    """
    return BrowserSession(platform, bridge, max_wait=max_wait)


async def get_current_platform() -> str | None:
    """Get the platform currently holding the browser lock."""
    try:
        r = await _get_redis()
        return await r.get(_PLATFORM_KEY)
    except Exception:
        return None


async def get_queue_length() -> int:
    """Get the number of workers waiting for the browser."""
    try:
        r = await _get_redis()
        return await r.llen(_QUEUE_KEY)
    except Exception:
        return 0


async def force_release_lock() -> bool:
    """Force-release the browser lock (admin/debug use only)."""
    try:
        r = await _get_redis()
        await r.delete(_LOCK_KEY)
        await r.delete(_PLATFORM_KEY)
        await r.delete(_QUEUE_KEY)
        logger.warning("Browser orchestrator: lock force-released")
        return True
    except Exception as exc:
        logger.error("Browser orchestrator: force-release failed: %s", exc)
        return False
