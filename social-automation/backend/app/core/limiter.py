import os
import uuid

from slowapi import Limiter
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
)
