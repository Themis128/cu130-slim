"""Twitter/X profile update service via tweepy (v1.1 API).

This module wraps tweepy's v1.1 ``API.update_profile``,
``API.update_profile_image``, and ``API.update_profile_banner`` methods
to enable profile metadata writes that the v2 free tier does not support.

Requirements:
    - Twitter API v1.1 credentials (API key/secret + access token/secret)
    - Twitter Basic ($100/mo) or Pro tier — profile writes are not
      available on the free tier.

Config:
    TWITTER_API_KEY, TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
"""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


class TwitterProfileError(Exception):
    """Raised when a Twitter profile operation fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Twitter profile error {status_code}: {detail}")


class TwitterProfileService:
    """Profile update service using tweepy's v1.1 API.

    Created per-request with the account's v1.1 credentials.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
    ) -> None:
        try:
            import tweepy
        except ImportError as e:
            raise TwitterProfileError(
                500,
                "tweepy is not installed. Run: pip install tweepy>=4.14.0",
            ) from e

        auth = tweepy.OAuth1UserHandler(
            api_key,
            api_secret,
            access_token,
            access_token_secret,
        )
        self._api = tweepy.API(auth, wait_on_rate_limit=True)

    def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's profile via verify_credentials."""
        try:
            user = self._api.verify_credentials()
        except Exception as e:
            raise TwitterProfileError(401, f"Twitter authentication failed: {e}") from e

        return {
            "id": str(user.id),
            "username": user.screen_name,
            "full_name": user.name,
            "biography": user.description,
            "location": user.location,
            "website": user.url,
            "profile_pic_url": user.profile_image_url_https.replace("_normal", "") if user.profile_image_url_https else None,
            "cover_url": user.profile_banner_url if hasattr(user, "profile_banner_url") else None,
            "followers": user.followers_count,
            "is_verified": getattr(user, "verified", False),
            "raw": user._json if hasattr(user, "_json") else {},
        }

    def update_profile(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        location: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        """Update the authenticated user's profile fields.

        Only fields that are set will be updated.
        """
        kwargs: dict[str, str] = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if location is not None:
            kwargs["location"] = location
        if url is not None:
            kwargs["url"] = url

        if not kwargs:
            raise TwitterProfileError(400, "At least one field must be provided")

        try:
            user = self._api.update_profile(**kwargs)
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter profile update failed: {e}") from e

        return {
            "success": True,
            "updated_fields": list(kwargs.keys()),
            "username": user.screen_name,
        }

    def update_profile_image(self, image_bytes: bytes) -> dict[str, Any]:
        """Upload a new profile picture."""
        try:
            file_obj = io.BytesIO(image_bytes)
            user = self._api.update_profile_image(filename="profile.jpg", file=file_obj)
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter profile image upload failed: {e}") from e

        return {
            "success": True,
            "updated_fields": ["profile_picture"],
            "username": user.screen_name,
        }

    def update_profile_banner(self, image_bytes: bytes) -> dict[str, Any]:
        """Upload a new profile banner."""
        try:
            file_obj = io.BytesIO(image_bytes)
            self._api.update_profile_banner(filename="banner.jpg", file=file_obj)
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter banner upload failed: {e}") from e

        return {
            "success": True,
            "updated_fields": ["banner"],
        }
