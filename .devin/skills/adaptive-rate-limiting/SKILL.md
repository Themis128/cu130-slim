# Adaptive Rate Limiting

Patterns for reliable social media API request throttling, backoff, and proxy management.
Based on research from instagrapi best practices, auto_connector, and ProxyRotator.

## Core Principles

1. **Stable proxy identity**: One stable proxy/IP per account. Match country, locale,
   device settings, and saved sessions. Never rotate proxy mid-session.
2. **Honor Retry-After**: When a platform returns 429 with a `Retry-After` header, wait
   exactly that long before the next request. Don't guess — use the platform's hint.
3. **Exponential backoff with jitter**: On transient failures, wait `base * 2^attempt +
   random_jitter`. Jitter prevents thundering herd when multiple workers retry simultaneously.
4. **Per-worker pacing**: Each Celery worker should pace its own requests. Don't rely on
   a global rate limiter alone — workers may be on different machines.
5. **Two-layer cache**: In-memory hot layer (for the current request) + Redis TTL layer
   (for cross-worker sharing). Cache platform responses to avoid redundant API calls.

## SocialAuto Implementation

SocialAuto already has:
- Cloudflare WARP proxy (`warp-proxy:1080`) as the default SOCKS5 proxy
- Per-account proxy assignment via `INSTAGRAM_PROXY` env var
- Redis for cross-worker coordination
- Celery beat for scheduled tasks
- D1 circuit breaker for database writes

### Recommended Additions

#### 1. Retry-After Header Handling

```python
# In app/services/publishing.py or a new app/services/rate_limiter.py
import asyncio
import random
from datetime import datetime, UTC

async def with_retry_after(
    func,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    **kwargs,
):
    """Call a platform API function with Retry-After-aware backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            # Check for Retry-After header in the exception
            retry_after = _extract_retry_after(exc)
            if retry_after:
                delay = min(retry_after, max_delay)
            else:
                jitter = random.uniform(0, 0.5)
                delay = min(base_delay * (2 ** attempt) + jitter, max_delay)
            await asyncio.sleep(delay)

def _extract_retry_after(exc) -> float | None:
    """Extract Retry-After value from an HTTP exception."""
    if hasattr(exc, 'response') and exc.response is not None:
        headers = getattr(exc.response, 'headers', {})
        retry_after = headers.get('Retry-After') or headers.get('retry-after')
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None
```

#### 2. Per-Account Request Pacing

```python
# In app/services/rate_limiter.py
import redis.asyncio as redis
from app.core.config import settings

class PerAccountRateLimiter:
    """Redis-based per-account rate limiter for platform API calls."""

    def __init__(self):
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self, account_id: str, platform: str, max_per_hour: int = 100):
        """Block until it's safe to make a request for this account."""
        r = await self._get_redis()
        key = f"rate:{platform}:{account_id}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 3600)  # 1 hour window
        if count > max_per_hour:
            ttl = await r.ttl(key)
            if ttl > 0:
                await asyncio.sleep(ttl)
            await r.delete(key)

    async def cooldown(self, account_id: str, platform: str, seconds: int):
        """Set a cooldown period for an account (e.g., after a 429)."""
        r = await self._get_redis()
        key = f"cooldown:{platform}:{account_id}"
        await r.setex(key, seconds, "1")
```

#### 3. Platform-Specific Rate Limits

| Platform | Endpoint | Limit | Window |
|----------|----------|-------|--------|
| LinkedIn | Share API | 150 posts | per day |
| LinkedIn | Marketing API | 100K calls | per day |
| Twitter/X | v2 API | 50 posts | per 24h |
| Twitter/X | v2 API | 300 reads | per 15 min |
| Instagram | Graph API | 25 posts | per 24h |
| Instagram | Graph API | 200 calls | per hour |
| Threads | Publishing | 25 posts | per 24h |
| TikTok | Content API | 6 videos | per 24h |
| Facebook | Graph API | 200 calls | per hour |

## Usage in Workers

```python
# In app/worker/tasks/publishing.py
from app.services.rate_limiter import PerAccountRateLimiter

rate_limiter = PerAccountRateLimiter()

async def publish_to_platform(account, post):
    # Wait for rate limit clearance
    await rate_limiter.acquire(account.id, account.platform, max_per_hour=50)

    try:
        result = await with_retry_after(
            platform_publish_func,
            account=account,
            post=post,
        )
    except RateLimitError:
        # Set a cooldown and requeue
        await rate_limiter.cooldown(account.id, account.platform, seconds=3600)
        raise RetryException(countdown=3600)
```

## Free/Open-Source Tools Referenced

- **instagrapi**: https://github.com/subzeroid/instagrapi (MIT) — Instagram private API with
  rate limiting best practices
- **ProxyRotator**: https://github.com/keyhankamyar/ProxyRotator — Async V2ray proxy rotation
- **auto_connector**: https://github.com/ivasik-k7/auto_connector — Adaptive throttling with
  Retry-After handling
- **Cloudflare WARP**: Free SOCKS5 proxy, already integrated as `warp-proxy` container
