"""Twitter/X profile update service via requests-oauthlib (v1.1 API).

Uses raw OAuth1Session calls to the v1.1 ``account/*`` endpoints —
``verify_credentials``, ``update_profile``, ``update_profile_image``,
``update_profile_banner`` — which the v2 free tier does not support.
requests-oauthlib is used instead of tweepy so the stack can pin
``oauthlib>=4`` (CVE-2026-49272); tweepy caps it at ``<4``.

Requirements:
    - Twitter API v1.1 credentials (API key/secret + access token/secret)
    - Twitter Basic ($100/mo) or Pro tier — profile writes are not
      available on the free tier.

Config:
    TWITTER_API_KEY, TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_BASE = "https://api.twitter.com/1.1/account"


class TwitterProfileError(Exception):
    """Raised when a Twitter profile operation fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Twitter profile error {status_code}: {detail}")


class TwitterProfileService:
    """Profile update service using OAuth1Session's v1.1 calls.

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
            from requests_oauthlib import OAuth1Session
        except ImportError as e:
            raise TwitterProfileError(
                500,
                "requests-oauthlib is not installed.",
            ) from e

        self._api = OAuth1Session(
            api_key,
            api_secret,
            access_token,
            access_token_secret,
        )

    def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's profile via verify_credentials."""
        try:
            r = self._api.get(f"{_BASE}/verify_credentials.json")
            user = r.json() if r.status_code == 200 else None
        except Exception as e:
            raise TwitterProfileError(401, f"Twitter authentication failed: {e}") from e
        if user is None:
            raise TwitterProfileError(401, f"Twitter authentication failed: {r.status_code}")

        pic = user.get("profile_image_url_https")
        return {
            "id": str(user.get("id", "")),
            "username": user.get("screen_name", ""),
            "full_name": user.get("name", ""),
            "biography": user.get("description", ""),
            "location": user.get("location", ""),
            "website": user.get("url"),
            "profile_pic_url": pic.replace("_normal", "") if pic else None,
            "cover_url": user.get("profile_banner_url"),
            "followers": user.get("followers_count", 0),
            "is_verified": user.get("verified", False),
            "raw": user,
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
            r = self._api.post(f"{_BASE}/update_profile.json", data=kwargs)
            user = r.json() if r.status_code == 200 else {}
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter profile update failed: {e}") from e
        if r.status_code != 200:
            raise TwitterProfileError(500, f"Twitter profile update failed: {r.status_code}")

        return {
            "success": True,
            "updated_fields": list(kwargs.keys()),
            "username": user.get("screen_name", ""),
        }

    def update_profile_image(self, image_bytes: bytes) -> dict[str, Any]:
        """Upload a new profile picture."""
        try:
            r = self._api.post(
                f"{_BASE}/update_profile_image.json",
                files={"image": ("profile.jpg", image_bytes)},
            )
            user = r.json() if r.status_code == 200 else {}
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter profile image upload failed: {e}") from e
        if r.status_code != 200:
            raise TwitterProfileError(500, f"Twitter profile image upload failed: {r.status_code}")

        return {
            "success": True,
            "updated_fields": ["profile_picture"],
            "username": user.get("screen_name", ""),
        }

    def update_profile_banner(self, image_bytes: bytes) -> dict[str, Any]:
        """Upload a new profile banner."""
        try:
            r = self._api.post(
                f"{_BASE}/update_profile_banner.json",
                files={"banner": ("banner.jpg", image_bytes)},
            )
        except Exception as e:
            raise TwitterProfileError(500, f"Twitter banner upload failed: {e}") from e
        if r.status_code != 200:
            raise TwitterProfileError(500, f"Twitter banner upload failed: {r.status_code}")

        return {
            "success": True,
            "updated_fields": ["banner"],
        }
