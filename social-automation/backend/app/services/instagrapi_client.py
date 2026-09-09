"""Direct instagrapi client for Instagram publishing via private mobile API.

Uses username/password login instead of the official Graph API.  Session is
cached as a JSON settings file on the shared uploads volume so re-login is
only needed when the session expires or is explicitly cleared.

All blocking instagrapi calls are dispatched via asyncio.to_thread so the
async event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.free_instagram_client import FreeInstagramError, free_instagram_client
from app.services.hikerapi_client import HikerAPIError, hiker_client

logger = logging.getLogger(__name__)

# Session file lives on the uploads volume (shared between api and workers).
_SESSION_DIR = Path("/app/uploads/.instagrapi")
_SESSION_TTL = 86400 * 6  # 6 days — refresh well before IG's ~90-day limit

try:
    from instagrapi.exceptions import (
        ChallengeRequired,
        ClientThrottledError,
        FeedbackRequired,
        LoginRequired,
        PleaseWaitFewMinutes,
        ProxyAddressIsBlocked,
        RateLimitError,
    )
except Exception:

    class _DummyException(Exception):
        pass

    ChallengeRequired = (
        ClientThrottledError
    ) = (
        FeedbackRequired
    ) = LoginRequired = PleaseWaitFewMinutes = ProxyAddressIsBlocked = RateLimitError = _DummyException


class InstagrapiError(Exception):
    """Raised when an instagrapi operation fails."""


class _RedisRateLimiter:
    """Sliding-window per-account request limiter backed by Redis."""

    def __init__(self, redis_url: str, rpm: int) -> None:
        self.rpm = rpm
        self._redis: Any | None = None
        self._key_prefix = "ig_ratelimit"
        if redis_url and rpm > 0:
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(redis_url, decode_responses=True)
            except Exception as exc:
                logger.warning("instagrapi: could not connect to Redis rate limiter: %s", exc)

    async def wait(self, account: str) -> None:
        if not self._redis:
            return
        key = f"{self._key_prefix}:{account}"
        now = time.time()
        while True:
            await self._redis.zremrangebyscore(key, 0, now - 60)
            count = await self._redis.zcard(key)
            if count < self.rpm:
                await self._redis.zadd(key, {str(now): now})
                return
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            wait_for = 60 - (now - float(oldest[0][1])) if oldest else 5.0
            await asyncio.sleep(max(1.0, wait_for))


class InstagrapiClient:
    """Async-compatible wrapper around instagrapi.Client.

    Creates and caches one Client per username.  Call ``upload_photo``,
    ``upload_video``, or ``upload_album`` — each method ensures the client is
    logged in before executing.
    """

    def __init__(
        self,
        username: str,
        password: str,
        proxy: str | None = None,
        rate_limit: bool = True,
    ) -> None:
        self._username = username.strip()
        self._password = password
        self._session_file = _SESSION_DIR / f"{self._username}.json"
        self._client: Any | None = None
        self._proxy_index = 0
        self._settings = get_settings()
        self._proxies = self._build_proxy_list(proxy)
        rpm = self._settings.INSTAGRAM_RATE_LIMIT_RPM if rate_limit else 0
        self._rate_limiter = _RedisRateLimiter(self._settings.REDIS_URL, rpm)
        self._max_retries = 3

    def _build_proxy_list(self, proxy: str | None) -> list[str]:
        pool = [p.strip() for p in (self._settings.INSTAGRAM_PROXY_POOL or "").split(",") if p.strip()]
        primary = (proxy or self._settings.INSTAGRAM_PROXY or "socks5://warp-proxy:1080").strip()
        if primary and primary not in pool:
            return [primary, *pool]
        return pool or [primary]

    @property
    def _current_proxy(self) -> str:
        return self._proxies[self._proxy_index % len(self._proxies)]

    # ── Internal sync helpers (called from threads) ───────────────────────

    def _build_client(self) -> Any:
        """Instantiate a fresh instagrapi.Client with optional proxy."""
        from instagrapi import Client

        cl = Client()
        proxy = self._current_proxy
        if proxy:
            try:
                cl.set_proxy(proxy)
            except Exception as exc:
                logger.warning("instagrapi: could not set proxy %s: %s", proxy, exc)
        return cl

    def _load_session(self, cl: Any) -> bool:
        """Try to restore a previously saved session. Returns True on success."""
        if not self._session_file.exists():
            return False
        age = time.time() - self._session_file.stat().st_mtime
        if age > _SESSION_TTL:
            self._session_file.unlink(missing_ok=True)
            return False
        try:
            settings = json.loads(self._session_file.read_text())
            cl.set_settings(settings)
            # Re-login using saved session cookies; avoids a full password handshake.
            cl.relogin()
            logger.info("instagrapi: restored session for %s", self._username)
            return True
        except Exception as exc:
            logger.warning("instagrapi: session restore failed (%s); doing fresh login", exc)
            self._session_file.unlink(missing_ok=True)
            return False

    def _save_session(self, cl: Any) -> None:
        try:
            _SESSION_DIR.mkdir(parents=True, exist_ok=True)
            self._session_file.write_text(json.dumps(cl.get_settings()))
        except Exception as exc:
            logger.warning("instagrapi: could not save session: %s", exc)

    def _ensure_client(self) -> Any:
        """Return a logged-in client; login/restore as needed (sync)."""
        if self._client is not None:
            return self._client

        cl = self._build_client()

        if not self._load_session(cl):
            try:
                cl.login(self._username, self._password)
                logger.info("instagrapi: fresh login for %s", self._username)
            except Exception as exc:
                raise InstagrapiError(f"Instagram login failed for {self._username}: {exc}") from exc
            self._save_session(cl)

        self._client = cl
        return self._client

    def _invalidate(self) -> None:
        """Discard the cached client and session file (e.g. on session expiry)."""
        self._client = None
        self._session_file.unlink(missing_ok=True)

    def _rotate_proxy(self) -> None:
        """Move to the next proxy in the pool and discard the cached client."""
        self._proxy_index = (self._proxy_index + 1) % len(self._proxies)
        self._client = None
        logger.warning("instagrapi: rotated to proxy %s", self._current_proxy)

    def _backoff(self, attempt: int) -> None:
        sleep = min(120, 2**attempt) + attempt
        logger.warning("instagrapi: backing off %s seconds before retry %s", sleep, attempt)
        time.sleep(sleep)

    def _is_throttle(self, exc: Exception) -> bool:
        return isinstance(exc, ClientThrottledError | PleaseWaitFewMinutes | RateLimitError | FeedbackRequired)

    def _is_proxy_block(self, exc: Exception) -> bool:
        return isinstance(exc, ProxyAddressIsBlocked)

    def _is_session_error(self, exc: Exception) -> bool:
        if isinstance(exc, LoginRequired | ChallengeRequired):
            return True
        msg = str(exc).lower()
        return any(k in msg for k in ("login_required", "loginrequired", "not authorized", "403"))

    async def _wait_for_rate_limit(self) -> None:
        await self._rate_limiter.wait(self._username)

    def _sync_call(self, fn: Callable[[Any], Any]) -> Any:
        """Execute an instagrapi call with proxy/backoff/session retry logic."""
        for attempt in range(self._max_retries):
            try:
                cl = self._ensure_client()
                return fn(cl)
            except Exception as exc:
                if self._is_proxy_block(exc):
                    self._rotate_proxy()
                    if attempt == self._max_retries - 1:
                        raise InstagrapiError(f"Proxy exhausted for {self._username}: {exc}") from exc
                    continue
                if self._is_session_error(exc):
                    self._invalidate()
                    if attempt == self._max_retries - 1:
                        raise InstagrapiError(f"Instagram session failed for {self._username}: {exc}") from exc
                    continue
                if self._is_throttle(exc):
                    if attempt == self._max_retries - 1:
                        raise InstagrapiError(f"Instagram rate-limited for {self._username}: {exc}") from exc
                    self._backoff(attempt)
                    continue
                raise InstagrapiError(f"Instagram operation failed for {self._username}: {exc}") from exc
        raise InstagrapiError(f"Instagram operation failed for {self._username} after {self._max_retries} retries")

    # ── Public async API ──────────────────────────────────────────────────

    async def upload_photo(self, file_path: str, caption: str) -> dict[str, Any]:
        """Upload a single photo post. Returns dict with id, pk, code."""
        await self._wait_for_rate_limit()

        def _do(cl: Any) -> dict[str, Any]:
            media = cl.photo_upload(Path(file_path), caption)
            return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}

        return await asyncio.to_thread(self._sync_call, _do)

    async def upload_video(self, file_path: str, caption: str) -> dict[str, Any]:
        """Upload a single video post. Returns dict with id, pk, code."""
        await self._wait_for_rate_limit()

        def _do(cl: Any) -> dict[str, Any]:
            media = cl.video_upload(Path(file_path), caption)
            return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}

        return await asyncio.to_thread(self._sync_call, _do)

    async def upload_album(self, file_paths: list[str], caption: str) -> dict[str, Any]:
        """Upload a carousel/album post (2-10 mixed photos/videos)."""
        await self._wait_for_rate_limit()

        def _do(cl: Any) -> dict[str, Any]:
            media = cl.album_upload([Path(fp) for fp in file_paths], caption)
            return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}

        return await asyncio.to_thread(self._sync_call, _do)

    # ── Read-only profile (with free fallback chain) ──────────────────────

    async def get_profile(self) -> dict[str, Any]:
        """Get the current account's profile.

        Fallback chain on throttle/block:
        1. instagrapi private API (primary)
        2. Free Instagram client (sidecar anon + HTML scraper) — no cost
        3. HikerAPI (paid, only if configured) — last resort
        """
        try:
            return await asyncio.to_thread(self._sync_call, lambda cl: cl.user_info(cl.user_id))
        except InstagrapiError as exc:
            if not self._is_throttle_error(exc):
                raise
            logger.warning("instagrapi: throttled, falling back to free Instagram client")
            # Tier 2: free fallback (sidecar anon + HTML scraper)
            try:
                return await free_instagram_client.get_user_by_username(self._username)
            except FreeInstagramError:
                # Tier 3: HikerAPI (paid, only if configured)
                if not hiker_client.enabled:
                    raise
                logger.warning("instagrapi: free fallback failed, trying HikerAPI (paid)")
                try:
                    return await hiker_client.get_user_by_username(self._username)
                except HikerAPIError as hiker_exc:
                    raise InstagrapiError(
                        f"All fallbacks failed: instagrapi={exc}, free=exhausted, hiker={hiker_exc}"
                    ) from hiker_exc

    def _is_throttle_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in ("429", "throttle", "please_wait", "rate limit", "too many requests"))

    # ── Profile update (writes require auth) ──────────────────────────────

    async def update_profile(
        self,
        biography: str | None = None,
        external_url: str | None = None,
        full_name: str | None = None,
    ) -> dict[str, Any]:
        """Update the authenticated account's profile fields.

        Only updates fields that are provided (not None). Requires a valid
        authenticated session — this cannot use the free fallback chain.
        """
        await self._wait_for_rate_limit()

        data: dict[str, Any] = {}
        if biography is not None:
            data["biography"] = biography
        if external_url is not None:
            data["external_url"] = external_url
        if full_name is not None:
            data["full_name"] = full_name

        if not data:
            return {"status": "no_changes"}

        def _do(cl: Any) -> dict[str, Any]:
            account = cl.account_edit(**data)
            return {
                "status": "updated",
                "username": account.username,
                "full_name": account.full_name,
                "biography": account.biography,
                "external_url": str(account.external_url) if account.external_url else None,
            }

        return await asyncio.to_thread(self._sync_call, _do)
