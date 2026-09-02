"""Platform driver protocol and registry.

This provides a uniform abstraction over the per-platform publishing,
deletion, analytics, and follower-count functions that already live in
``app.services.publishing``, ``app.services.*_api``, and
``app.services.analytics_sync``.

The drivers are thin wrappers — they delegate to the existing functions
so we don't duplicate any platform logic.  The registry lets callers
resolve a driver by platform name without knowing the concrete class.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.models.content import Post
from app.models.social_account import SocialAccount
from app.services.publishing import PublishResult


@runtime_checkable
class PlatformDriver(Protocol):
    """Uniform interface for a social platform driver."""

    @property
    def platform(self) -> str:
        """Platform name (twitter, linkedin, instagram, …)."""
        ...

    async def publish(
        self,
        account: SocialAccount,
        post: Post,
        db: Any,
    ) -> PublishResult:
        """Publish ``post`` to ``account``."""
        ...

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        """Delete a previously published post. Returns True on success."""
        ...

    async def get_follower_count(self, account: SocialAccount) -> int:
        """Return the current follower count for ``account``."""
        ...


# ── concrete drivers ──────────────────────────────────────────────────────────


class _BaseDriver:
    """Base class with the platform name and shared helpers."""

    _platform: str = ""

    @property
    def platform(self) -> str:
        return self._platform

    async def publish(self, account: SocialAccount, post: Post, db: Any) -> PublishResult:
        from app.services.publishing import publish_to_platform
        return await publish_to_platform(account, post, db)

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        return False  # override in subclasses

    async def get_follower_count(self, account: SocialAccount) -> int:
        return 0  # override in subclasses


class TwitterDriver(_BaseDriver):
    _platform = "twitter"

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        from app.core.security import decrypt_token
        from app.services.twitter_api import TwitterAPIClient
        token = decrypt_token(bytes(account.access_token_enc))
        client = TwitterAPIClient(access_token=token)
        await client.delete_tweet(platform_post_id)
        return True

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _twitter_follower_count
        return await _twitter_follower_count(account)


class LinkedInDriver(_BaseDriver):
    _platform = "linkedin"

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        from app.core.security import decrypt_token
        from app.services.linkedin_api import LinkedInAPIClient
        token = decrypt_token(bytes(account.access_token_enc))
        client = LinkedInAPIClient(access_token=token)
        await client.delete_post(platform_post_id)
        return True

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _linkedin_follower_count
        return await _linkedin_follower_count(account)


class FacebookDriver(_BaseDriver):
    _platform = "facebook"

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        from app.core.security import decrypt_token
        from app.services.facebook_api import FacebookAPIClient
        token = decrypt_token(bytes(account.access_token_enc))
        client = FacebookAPIClient(access_token=token)
        await client.delete_post(platform_post_id)
        return True

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _facebook_follower_count
        return await _facebook_follower_count(account)


class InstagramDriver(_BaseDriver):
    _platform = "instagram"

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _instagram_follower_count
        return await _instagram_follower_count(account)


class ThreadsDriver(_BaseDriver):
    _platform = "threads"

    async def delete(self, account: SocialAccount, platform_post_id: str) -> bool:
        from app.core.security import decrypt_token
        from app.services.threads_api import ThreadsAPIClient
        token = decrypt_token(bytes(account.access_token_enc))
        client = ThreadsAPIClient(access_token=token)
        await client.delete_post(platform_post_id)
        return True

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _threads_follower_count
        return await _threads_follower_count(account)


class TikTokDriver(_BaseDriver):
    _platform = "tiktok"

    async def get_follower_count(self, account: SocialAccount) -> int:
        from app.api.analytics import _tiktok_follower_count
        return await _tiktok_follower_count(account)


# ── registry ──────────────────────────────────────────────────────────────────

_DRIVERS: dict[str, _BaseDriver] = {
    "twitter": TwitterDriver(),
    "linkedin": LinkedInDriver(),
    "facebook": FacebookDriver(),
    "instagram": InstagramDriver(),
    "threads": ThreadsDriver(),
    "tiktok": TikTokDriver(),
}


def get_driver(platform: str) -> PlatformDriver | None:
    """Return the driver for ``platform`` or ``None`` if unsupported."""
    return _DRIVERS.get(platform)
