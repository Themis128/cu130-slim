"""TikTok profile service — official API for reads, self-hosted private API for writes.

The official TikTok Display API is read-only for profile fields.  To update
bio, nickname, username, or avatar we use TikTok's private mobile API
(``api-h2.tiktokv.com``) with a self-hosted X-Gorgon / X-Khronos request
signer.  No third-party signing service is required.

Auth for the private API uses a ``sessionid`` cookie extracted from a logged-in
TikTok web session.  The cookie is stored in the SecretStore under
``TIKTOK_SESSION_ID``.  The numeric ``user_id`` is stored under
``TIKTOK_USER_ID``.

Config (via SecretStore or settings):
    TIKTOK_SESSION_ID — sessionid cookie from tiktok.com login
    TIKTOK_USER_ID   — numeric TikTok user ID (e.g. 6919176684098028549)

Available profile operations:
    - get profile (official Display API + private API fallback)
    - set nickname (private API)
    - set signature/bio (private API)
    - set unique ID / username (private API)
    - check username availability (private API)
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# TikTok private mobile API base URL
_PRIVATE_API_BASE = "https://api-h2.tiktokv.com"

# Default device params mimicking an Android TikTok app session.
# These are static defaults — the signing happens via X-Gorgon.
_DEFAULT_PARAMS: dict[str, Any] = {
    "manifest_version_code": 190103,
    "_rticker": 0,  # set per-request
    "current_region": "GR",
    "app_language": "en",
    "app_type": "normal",
    "iid": 7183409061831001857,
    "channel": "googleplay",
    "device_type": "ASUS_Z01QD",
    "language": "en",
    "cpu_support64": "true",
    "host_abi": "armeabi-v7a",
    "locale": "en",
    "resolution": 1600 * 900,
    "openudid": "7f8e923db4b22341",
    "update_version_code": 190103,
    "ac2": "wifi",
    "cdid": "c7357243-a13f-4d42-94d9-cb318ae73c52",
    "sys_region": "US",
    "os_api": 28,
    "uoo": 0,
    "timezone_name": "Europe/Athens",
    "dpi": 300,
    "residence": "GR",
    "carrier_region": "GR",
    "ac": "wifi",
    "device_id": 7147445232161539590,
    "mcc_mnc": 20210,
    "os_version": 9,
    "timezone_offset": 7200,
    "version_code": 190103,
    "app_name": "trill",
    "ab_version": "19.1.3",
    "version_name": "19.1.3",
    "device_brand": "Asus",
    "op_region": "GR",
    "ssmix": "a",
    "device_platform": "android",
    "build_number": "19.1.3",
    "region": "GR",
    "aid": 1180,
    "ts": 0,  # set per-request
}

# X-Gorgon signing key (from the open-source HkerVit implementation)
_GORGON_KEY = [
    0xDF, 0x77, 0xB9, 0x40, 0xB9, 0x9B, 0x84, 0x83,
    0xD1, 0xB9, 0xCB, 0xD1, 0xF7, 0xC2, 0xB9, 0x85,
    0xC3, 0xD0, 0xFB, 0xC3,
]


class TikTokProfileError(Exception):
    """Raised when a TikTok profile operation fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"TikTok profile error {status_code}: {detail}")


# ── X-Gorgon / X-Khronos request signing (self-hosted) ───────────────────────


def _md5_hex(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()


def _rbit(num: int) -> int:
    """Reverse the bits of a single byte."""
    bits = bin(num)[2:].zfill(8)
    return int(bits[::-1], 2)


def _hex_byte(num: int) -> str:
    return hex(num)[2:].zfill(2)


def _reverse_nibble(num: int) -> int:
    """Swap the two hex nibbles of a byte."""
    s = _hex_byte(num)
    return int(s[1:] + s[:1], 16)


def _sign_gorgon(params: str, data: str | None, cookies: str) -> dict[str, str]:
    """Compute X-Gorgon and X-Khronos headers for a TikTok private API request.

    Based on the open-source implementation from HkerVit/TikTok-Private-API.
    No external signing service required.
    """
    # Build the Gorgon payload: md5(params) + md5(data) + md5(cookies) + zeros
    gorgon = _md5_hex(params)
    gorgon += _md5_hex(data) if data else "0" * 32
    gorgon += _md5_hex(cookies) if cookies else "0" * 32
    gorgon += "0" * 32

    unix = int(time.time())
    key_len = 0x14

    # Extract 12 bytes (3 groups of 4) from the gorgon hex string
    param_list: list[int] = []
    for i in range(0, 12, 4):
        chunk = gorgon[8 * i : 8 * (i + 1)]
        for j in range(4):
            param_list.append(int(chunk[j * 2 : (j + 1) * 2], 16))

    # Append constant + timestamp bytes
    param_list.extend([0x0, 0x6, 0xB, 0x1C])
    h = unix
    param_list.append((h & 0xFF000000) >> 24)
    param_list.append((h & 0x00FF0000) >> 16)
    param_list.append((h & 0x0000FF00) >> 8)
    param_list.append((h & 0x000000FF) >> 0)

    # XOR with key
    eor: list[int] = [a ^ b for a, b in zip(param_list, _GORGON_KEY)]

    # Mixing pass
    for i in range(key_len):
        c = _reverse_nibble(eor[i])
        d = eor[(i + 1) % key_len]
        e = c ^ d
        f = _rbit(e)
        eor[i] = ((f ^ 0xFFFFFFFF) ^ key_len) & 0xFF

    result = "".join(_hex_byte(b) for b in eor)
    return {"X-Gorgon": "0404b0d30000" + result, "X-Khronos": str(unix)}


# ── Private API client ───────────────────────────────────────────────────────


class _TikTokPrivateClient:
    """Low-level TikTok private mobile API client with self-hosted signing."""

    def __init__(self, session_id: str, user_id: int) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._cookie = f"sessionid={session_id}"

    def _build_params(self) -> dict[str, Any]:
        now_ms = int(round(time.time() * 1000))
        params = dict(_DEFAULT_PARAMS)
        params["_rticker"] = now_ms
        params["ts"] = int(time.time())
        return params

    @staticmethod
    def _query(data: dict[str, Any]) -> str:
        return urllib.parse.urlencode(data)

    def _headers(
        self, params_str: str, data_str: str | None = None
    ) -> dict[str, str]:
        headers = {
            "accept-encoding": "gzip",
            "connection": "Keep-Alive",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "host": "api-h2.tiktokv.com",
            "passport-sdk-version": "19",
            "sdk-version": "2",
            "user-agent": "okhttp/3.10.0.1",
            "x-ss-req-ticket": str(int(time.time() * 1000)),
        }
        sig = _sign_gorgon(params_str, data_str, self._cookie)
        headers.update(sig)
        if data_str:
            headers["x-ss-stub"] = hashlib.md5(data_str.encode()).hexdigest().upper()
        return headers

    async def _post(
        self, path: str, data: dict[str, Any], use_user_id: bool = True
    ) -> dict[str, Any]:
        params = self._build_params()
        params_str = self._query(params)
        if use_user_id:
            data["uid"] = self.user_id
            data["page_from"] = 0
            data["confirmed"] = 0
        data_str = self._query(data)
        headers = self._headers(params_str, data_str)
        url = f"{_PRIVATE_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                params=params,
                data=data_str,
                headers=headers,
                cookies={"sessionid": self.session_id},
            )
            logger.debug("TikTok private API %s -> %s", path, resp.status_code)
            if resp.status_code >= 400:
                raise TikTokProfileError(
                    resp.status_code,
                    f"Private API error: {resp.text[:300]}",
                )
            return resp.json()

    async def _get(
        self, path: str, extra_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params = self._build_params()
        if extra_params:
            params.update(extra_params)
        params_str = self._query(params)
        headers = self._headers(params_str, None)
        url = f"{_PRIVATE_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers=headers,
                cookies={"sessionid": self.session_id},
            )
            if resp.status_code >= 400:
                raise TikTokProfileError(
                    resp.status_code,
                    f"Private API error: {resp.text[:300]}",
                )
            return resp.json()

    # ── Profile operations ───────────────────────────────────────────────

    async def edit_bio(self, bio: str) -> dict[str, Any]:
        """Set the bio/signature text."""
        return await self._post(
            "/aweme/v1/commit/user/",
            {"signature": bio},
        )

    async def edit_nickname(self, nickname: str) -> dict[str, Any]:
        """Set the display name (nickname)."""
        return await self._post(
            "/aweme/v1/commit/user/",
            {"nickname": nickname},
        )

    async def edit_username(self, username: str) -> dict[str, Any]:
        """Set the unique ID (username). Can only be changed once every 30 days."""
        # Username changes use a different endpoint and don't include confirmed/page_from
        return await self._post(
            "/passport/login_name/update/",
            {"username": username},
            use_user_id=False,
        )

    async def check_username(self, username: str) -> dict[str, Any]:
        """Check if a username is available."""
        return await self._get(
            "/aweme/v1/unique/id/check/",
            extra_params={"unique_id": username},
        )


# ── Public service ───────────────────────────────────────────────────────────


class TikTokProfileService:
    """Profile read/write service for TikTok.

    Reads use the official Display API (via ``TikTokAPIClient``).
    Writes use the private mobile API with self-hosted X-Gorgon signing.
    """

    def __init__(
        self,
        access_token: str | None = None,
        open_id: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
    ) -> None:
        self.access_token = access_token
        self.open_id = open_id
        self._private: _TikTokPrivateClient | None = None
        if session_id and user_id:
            self._private = _TikTokPrivateClient(session_id, user_id)

    @property
    def can_write(self) -> bool:
        """Whether profile writes are available (requires session_id + user_id)."""
        return self._private is not None

    async def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's TikTok profile via official Display API."""
        if not self.access_token or not self.open_id:
            raise TikTokProfileError(503, "Official API token not configured")

        fields = (
            "open_id,union_id,avatar_url,display_name,bio_description,"
            "username,profile_deep_link,is_verified"
        )
        url = "https://open.tiktokapis.com/v2/user/info/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"fields": fields},
            )
        if resp.status_code >= 400:
            raise TikTokProfileError(resp.status_code, resp.text[:300])
        data = resp.json()
        error = data.get("error") or {}
        if error.get("code") and error["code"] != "ok":
            raise TikTokProfileError(400, error.get("message", "Unknown error"))
        user = data.get("data", {}).get("user", {})
        return {
            "id": user.get("open_id", ""),
            "username": user.get("username"),
            "full_name": user.get("display_name"),
            "biography": user.get("bio_description"),
            "profile_pic_url": user.get("avatar_url"),
            "is_verified": user.get("is_verified", False),
            "profile_deep_link": user.get("profile_deep_link"),
            "raw": data,
        }

    async def update_nickname(self, nickname: str) -> dict[str, Any]:
        """Update the display name (nickname)."""
        if not self._private:
            raise TikTokProfileError(503, "Private API session not configured")
        result = await self._private.edit_nickname(nickname)
        return {"success": True, "updated_fields": ["nickname"], "raw": result}

    async def update_signature(self, signature: str) -> dict[str, Any]:
        """Update the bio/signature text."""
        if not self._private:
            raise TikTokProfileError(503, "Private API session not configured")
        result = await self._private.edit_bio(signature)
        return {"success": True, "updated_fields": ["signature"], "raw": result}

    async def update_unique_id(self, unique_id: str) -> dict[str, Any]:
        """Update the username (unique ID). Can only be changed once every 30 days."""
        if not self._private:
            raise TikTokProfileError(503, "Private API session not configured")
        result = await self._private.edit_username(unique_id)
        return {"success": True, "updated_fields": ["unique_id"], "raw": result}

    async def check_username(self, username: str) -> dict[str, Any]:
        """Check if a username is available."""
        if not self._private:
            raise TikTokProfileError(503, "Private API session not configured")
        return await self._private.check_username(username)
