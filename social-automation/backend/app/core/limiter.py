import os
import uuid

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()


def _rate_limit_key(request) -> str:
    # During pytest runs, give every request a unique key so tests that share
    # the same client IP (127.0.0.1) do not trip the production rate limits.
    # The APP_ENV override is a convenience for local test runs.
    if os.getenv("PYTEST_CURRENT_TEST") or settings.APP_ENV in ("test", "development"):
        return str(uuid.uuid4())
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=settings.REDIS_URL,
    default_limits=["300/minute"],
)

# slowapi 0.1.10 middleware catches ANY exception from _check_request_limit
# (e.g. Redis/storage outage) and falls back to _rate_limit_exceeded_handler,
# which then crashes on exc.detail -> 500 on every route incl. /health.
# Re-raise storage failures as RateLimitExceeded so the real handler emits a
# clean 429 instead of an AttributeError crash-loop.
_orig_check_request_limit = limiter._check_request_limit


def _safe_check_request_limit(request, endpoint_func, in_middleware=True):
    try:
        return _orig_check_request_limit(request, endpoint_func, in_middleware)
    except RateLimitExceeded:
        raise
    except Exception as exc:
        raise RateLimitExceeded(
            detail=f"rate limit check unavailable: {type(exc).__name__}"
        ) from exc


limiter._check_request_limit = _safe_check_request_limit
