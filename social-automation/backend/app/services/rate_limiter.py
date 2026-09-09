"""Adaptive rate limiting for social media platform API calls.

Provides per-account request pacing, Retry-After header handling, and
exponential backoff with jitter. Uses Redis for cross-worker coordination.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Platform-specific rate limits (requests per hour per account)
PLATFORM_LIMITS: dict[str, int] = {
    "linkedin": 100,
    "twitter": 300,
    "instagram": 200,
    "threads": 100,
    "facebook": 200,
    "tiktok": 50,
}


class PerAccountRateLimiter:
    """Redis-based per-account rate limiter for platform API calls."""

    def __init__(self) -> None:
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis

            from app.core.config import settings

            self._redis = aioredis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self, account_id: str, platform: str, max_per_hour: int | None = None) -> None:
        """Block until it's safe to make a request for this account.

        Args:
            account_id: Social account UUID.
            platform: Platform name (linkedin, twitter, etc.).
            max_per_hour: Override the default platform limit.
        """
        limit = max_per_hour or PLATFORM_LIMITS.get(platform, 100)
        r = await self._get_redis()
        key = f"rate:{platform}:{account_id}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 3600)
        if count > limit:
            ttl = await r.ttl(key)
            if ttl > 0:
                logger.info(
                    "Rate limit reached for %s/%s (%d/%d), waiting %ds",
                    platform, account_id, count, limit, ttl,
                )
                await asyncio.sleep(ttl)
            await r.delete(key)

    async def cooldown(self, account_id: str, platform: str, seconds: int) -> None:
        """Set a cooldown period for an account (e.g., after a 429)."""
        r = await self._get_redis()
        key = f"cooldown:{platform}:{account_id}"
        await r.setex(key, seconds, "1")
        logger.info("Cooldown set for %s/%s: %ds", platform, account_id, seconds)

    async def is_cooled_down(self, account_id: str, platform: str) -> bool:
        """Check if an account is in cooldown."""
        r = await self._get_redis()
        key = f"cooldown:{platform}:{account_id}"
        return bool(await r.exists(key))


# Singleton
rate_limiter = PerAccountRateLimiter()


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After value from an HTTP exception."""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None


async def with_retry_after(
    func: Any,
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    **kwargs: Any,
) -> Any:
    """Call a platform API function with Retry-After-aware backoff.

    Args:
        func: Async callable to execute.
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay cap in seconds.
        **kwargs: Passed to func.

    Returns:
        Result of func on success.

    Raises:
        The last exception after all retries are exhausted.
    """
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            retry_after = _extract_retry_after(exc)
            if retry_after:
                delay = min(retry_after, max_delay)
                logger.info("Retry-After: waiting %.1fs (attempt %d/%d)", delay, attempt + 1, max_retries)
            else:
                jitter = random.uniform(0, 0.5)
                delay = min(base_delay * (2 ** attempt) + jitter, max_delay)
                logger.info("Backoff: waiting %.1fs (attempt %d/%d)", delay, attempt + 1, max_retries)
            await asyncio.sleep(delay)
    # Unreachable, but satisfies type checker
    raise RuntimeError("with_retry_after exhausted retries")


async def safe_publish(
    func: Any,
    account_id: str,
    platform: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Publish with rate limiting and retry-aware backoff.

    Combines per-account rate limiting with Retry-After-aware retries.
    On 429, sets a cooldown and re-raises so the caller can requeue.
    """
    await rate_limiter.acquire(account_id, platform)

    try:
        return await with_retry_after(func, *args, **kwargs)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            retry_after = _extract_retry_after(exc) or 3600
            await rate_limiter.cooldown(account_id, platform, int(retry_after))
        raise
