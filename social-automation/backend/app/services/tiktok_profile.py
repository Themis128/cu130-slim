"""TikTok profile update service via tiktok-private-api (tiktokflow).

This module wraps the unofficial TikTok private mobile API to enable
profile writes (nickname, signature/bio, avatar, unique ID) that the
official TikTok Display API does not support.

Requirements:
    - A signing server API key from tiktok-private-api.com
    - The signing server handles X-Argus / X-Ladon / X-Gorgon request
      signing.  Self-hosting the signer is possible but complex.

Config:
    TIKTOK_PRIVATE_API_KEY — signing server API key
    TIKTOK_SESSION_ID — optional, for session-based auth

Available profile operations:
    - get profile (profile_self)
    - set nickname (set_nickname)
    - set signature/bio (set_signature)
    - set unique ID / username (set_unique_id)
    - upload avatar (upload_avatar)
    - set privacy settings
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TikTokProfileError(Exception):
    """Raised when a TikTok profile operation fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"TikTok profile error {status_code}: {detail}")


class TikTokProfileService:
    """Profile update service using the TikTok private API.

    Created per-request with the signing server API key.
    """

    def __init__(self, api_key: str) -> None:
        try:
            from tiktokflow import TikTokAPI
        except ImportError as e:
            raise TikTokProfileError(
                500,
                "tiktok-private-api is not installed. Run: pip install tiktok-private-api",
            ) from e

        self._client = TikTokAPI(api_key=api_key)

    def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's TikTok profile."""
        try:
            data = self._client.user.profile_self()
        except Exception as e:
            raise TikTokProfileError(401, f"TikTok profile read failed: {e}") from e

        # Normalize the response
        user = data.get("user", data) if isinstance(data, dict) else {}
        return {
            "id": str(user.get("uid") or user.get("id", "")),
            "username": user.get("unique_id") or user.get("username"),
            "full_name": user.get("nickname"),
            "biography": user.get("signature"),
            "profile_pic_url": user.get("avatar_larger", {}).get("url") if isinstance(user.get("avatar_larger"), dict) else user.get("avatar_url"),
            "followers": user.get("follower_count"),
            "following": user.get("following_count"),
            "is_verified": user.get("is_verified", False),
            "is_private": user.get("is_private", False),
            "raw": data if isinstance(data, dict) else {},
        }

    def update_nickname(self, nickname: str) -> dict[str, Any]:
        """Update the display name (nickname)."""
        try:
            self._client.user.set_nickname(nickname=nickname)
        except Exception as e:
            raise TikTokProfileError(500, f"TikTok nickname update failed: {e}") from e
        return {"success": True, "updated_fields": ["nickname"]}

    def update_signature(self, signature: str) -> dict[str, Any]:
        """Update the bio/signature text."""
        try:
            self._client.user.set_signature(signature=signature)
        except Exception as e:
            raise TikTokProfileError(500, f"TikTok signature update failed: {e}") from e
        return {"success": True, "updated_fields": ["signature"]}

    def update_unique_id(self, unique_id: str) -> dict[str, Any]:
        """Update the username (unique ID). Can only be changed once every 7 days."""
        try:
            self._client.user.set_unique_id(unique_id=unique_id)
        except Exception as e:
            raise TikTokProfileError(500, f"TikTok unique ID update failed: {e}") from e
        return {"success": True, "updated_fields": ["unique_id"]}

    def upload_avatar(self, image_bytes: bytes) -> dict[str, Any]:
        """Upload a new profile picture."""
        try:
            import io
            self._client.user.upload_avatar(image=io.BytesIO(image_bytes))
        except Exception as e:
            raise TikTokProfileError(500, f"TikTok avatar upload failed: {e}") from e
        return {"success": True, "updated_fields": ["avatar"]}
