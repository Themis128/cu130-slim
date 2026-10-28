"""Unit tests for rate_limiter service."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.rate_limiter import (
    PLATFORM_LIMITS,
    PerAccountRateLimiter,
    _extract_retry_after,
    rate_limiter,
    safe_publish,
    with_retry_after,
)


class TestExtractRetryAfter:
    def test_no_response(self):
        assert _extract_retry_after(ValueError("test")) is None

    def test_with_retry_after_header(self):
        response = MagicMock()
        response.headers = {"Retry-After": "30"}
        exc = httpx.HTTPStatusError("test", request=MagicMock(), response=response)
        assert _extract_retry_after(exc) == 30.0

    def test_with_lowercase_header(self):
        response = MagicMock()
        response.headers = {"retry-after": "60"}
        exc = httpx.HTTPStatusError("test", request=MagicMock(), response=response)
        assert _extract_retry_after(exc) == 60.0

    def test_invalid_value(self):
        response = MagicMock()
        response.headers = {"Retry-After": "invalid"}
        exc = httpx.HTTPStatusError("test", request=MagicMock(), response=response)
        assert _extract_retry_after(exc) is None

    def test_no_header(self):
        response = MagicMock()
        response.headers = {}
        exc = httpx.HTTPStatusError("test", request=MagicMock(), response=response)
        assert _extract_retry_after(exc) is None


class TestWithRetryAfter:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        func = AsyncMock(return_value="success")
        result = await with_retry_after(func)
        assert result == "success"
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        func = AsyncMock(
            side_effect=[
                ValueError("fail"),
                ValueError("fail"),
                "success",
            ]
        )
        result = await with_retry_after(func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert func.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        func = AsyncMock(side_effect=ValueError("permanent fail"))
        with pytest.raises(ValueError):
            await with_retry_after(func, max_retries=2, base_delay=0.01)

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self):
        response = MagicMock()
        response.headers = {"Retry-After": "0.01"}
        exc = httpx.HTTPStatusError("429", request=MagicMock(), response=response)
        func = AsyncMock(side_effect=[exc, "success"])
        result = await with_retry_after(func, max_retries=2)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        func = AsyncMock(side_effect=[ValueError("fail"), "success"])
        # With huge base_delay but max_delay=0.01, should be fast
        result = await with_retry_after(func, max_retries=2, base_delay=1000, max_delay=0.01)
        assert result == "success"


class TestPerAccountRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        limiter = PerAccountRateLimiter()
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=0)
        limiter._redis = mock_redis

        await limiter.acquire("acc1", "linkedin", max_per_hour=100)
        assert mock_redis.incr.call_count == 1

    @pytest.mark.asyncio
    async def test_acquire_over_limit_waits(self):
        limiter = PerAccountRateLimiter()
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=101)
        mock_redis.expire = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=3600)
        mock_redis.delete = AsyncMock()
        limiter._redis = mock_redis

        with patch("asyncio.sleep", new=AsyncMock()):
            await limiter.acquire("acc1", "linkedin", max_per_hour=100)
        assert mock_redis.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_cooldown(self):
        limiter = PerAccountRateLimiter()
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        limiter._redis = mock_redis

        await limiter.cooldown("acc1", "linkedin", 60)
        assert mock_redis.setex.call_count == 1

    @pytest.mark.asyncio
    async def test_is_cooled_down(self):
        limiter = PerAccountRateLimiter()
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        limiter._redis = mock_redis

        result = await limiter.is_cooled_down("acc1", "linkedin")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_not_cooled_down(self):
        limiter = PerAccountRateLimiter()
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        limiter._redis = mock_redis

        result = await limiter.is_cooled_down("acc1", "linkedin")
        assert result is False


class TestSafePublish:
    @pytest.mark.asyncio
    async def test_success(self):
        func = AsyncMock(return_value="published")
        with patch.object(rate_limiter, "acquire", new=AsyncMock()):
            result = await safe_publish(func, "acc1", "linkedin")
        assert result == "published"

    @pytest.mark.asyncio
    async def test_429_sets_cooldown(self):
        response = MagicMock()
        response.status_code = 429
        response.headers = {"Retry-After": "60"}
        exc = httpx.HTTPStatusError("429", request=MagicMock(), response=response)
        func = AsyncMock(side_effect=exc)

        with patch.object(rate_limiter, "acquire", new=AsyncMock()), \
             patch.object(rate_limiter, "cooldown", new=AsyncMock()) as mock_cooldown:
            with pytest.raises(httpx.HTTPStatusError):
                await safe_publish(func, "acc1", "linkedin")
            assert mock_cooldown.call_count == 1


class TestPlatformLimits:
    def test_all_platforms_present(self):
        for platform in ["linkedin", "twitter", "instagram", "threads", "facebook", "tiktok"]:
            assert platform in PLATFORM_LIMITS
            assert PLATFORM_LIMITS[platform] > 0
