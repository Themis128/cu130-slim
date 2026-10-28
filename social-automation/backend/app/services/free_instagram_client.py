"""Free Instagram profile fallback — no API key, no cost.

Two-tier read-only fallback for Instagram profile data when the local
instagrapi private API is throttled (429) or proxy-blocked:

1. **aiograpi sidecar anonymous mode** — uses the existing
   ``instagram-private-api`` container (port 8011) with no login.
   Calls ``GET /user?username=...`` which fetches public profile data
   via Instagram's mobile API without authentication.

2. **HTML scraper** — fetches ``instagram.com/{username}/`` and parses
   the ``og:description`` meta tag + embedded JSON blob for follower
   counts, biography, and profile picture URL. Zero dependencies beyond
   httpx (already installed).

Both tiers work with public profiles only. No API key, no login, no cost.
"""
from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ── HTML scraper helpers ──────────────────────────────────────────────────

_PROFILE_URL = "https://www.instagram.com/{username}/"

_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "Instagram 320.0.0.0.0 (iPhone14,7; iOS 17_4; el_GR; GR; "
        "scale=3.00; 1080x1920; 542465234)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_WALL_MARKERS = (
    "loginform",
    "log in to instagram",
    "loginandsignuppage",
    "checkpoint",
    "challenge",
    "unusual activity",
    "access denied",
)


def _unabbrev(token: str) -> int | None:
    """Convert '104M' → 104000000, '4,853' → 4853."""
    m = re.match(r"^([\d.,]+)\s*([KkMmBb]?)$", token.strip())
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2).lower(), 1)
    return round(num * mult)


def _decode_unicode_escapes(s: str) -> str:
    """Decode \\ud83d\\ude80-style surrogate pairs and \\n escapes from JSON strings."""
    import json

    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


def _parse_og(html: str) -> dict[str, Any] | None:
    """Parse the og:description meta tag for approximate counts."""
    m = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    if not m:
        return None
    desc = html_module.unescape(m.group(1))
    c = re.search(
        r"([\d.,]+[KkMmBb]?)\s+Followers,\s*([\d.,]+[KkMmBb]?)\s+Following,"
        r"\s*([\d.,]+[KkMmBb]?)\s+Posts",
        desc,
        re.I,
    )
    if not c:
        return None
    who = re.search(r"from\s+@([A-Za-z0-9._]+)", desc)
    full_name_match = re.search(r"Instagram photos and videos from\s+(.+?)(?:\s*\(@|\s*$)", desc)
    return {
        "full_name": full_name_match.group(1).strip() if full_name_match else None,
        "username": who.group(1) if who else None,
        "follower_count": _unabbrev(c.group(1)),
        "following_count": _unabbrev(c.group(2)),
        "media_count": _unabbrev(c.group(3)),
        "og_description": desc,
    }


def _parse_embedded(html: str) -> dict[str, Any] | None:
    """Parse the embedded JSON blob for exact counts and biography."""
    m = re.search(r'"follower_count":\s*(\d+)', html)
    if not m:
        return None
    out: dict[str, Any] = {"follower_count": int(m.group(1))}
    for key in ("following_count", "media_count"):
        k = re.search(rf'"{key}":\s*(\d+)', html)
        if k:
            out[key] = int(k.group(1))
    bio = re.search(r'"biography":\s*"((?:[^"\\]|\\.)*)"', html)
    if bio:
        try:
            out["biography"] = _decode_unicode_escapes(bio.group(1))
        except Exception:
            out["biography"] = bio.group(1)
    pic = re.search(r'"profile_pic_url":\s*"((?:[^"\\]|\\.)*)"', html)
    if pic:
        try:
            out["profile_pic_url"] = _decode_unicode_escapes(pic.group(1))
        except Exception:
            out["profile_pic_url"] = pic.group(1)
    return out


def _parse_og_image(html: str) -> str | None:
    """Parse the og:image meta tag for the profile picture URL."""
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    return html_module.unescape(m.group(1)) if m else None


def _parse_meta_description(html: str) -> str | None:
    """Parse the meta description tag which contains the biography."""
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    if not m:
        return None
    desc = html_module.unescape(m.group(1))
    # Format: "0 Followers, 0 Following, 10 Posts - @cloudless.gr on Instagram: "bio text""
    bio_match = re.search(r'on Instagram:\s*"(.+?)""', desc)
    return bio_match.group(1) if bio_match else None


class FreeInstagramError(Exception):
    """Raised when all free fallback methods fail."""


class FreeInstagramClient:
    """Free, no-API-key Instagram profile reader.

    Tier 1: aiograpi sidecar anonymous mode (GET /user?username=...).
    Tier 2: HTML scraper (og:description + embedded JSON).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._sidecar_url = (
            self._settings.INSTAGRAM_PRIVATE_API_URL or "http://instagram-private-api:8000"
        ).rstrip("/")

    @property
    def enabled(self) -> bool:
        """Always enabled — no API key required."""
        return True

    # ── Tier 1: aiograpi sidecar anonymous ────────────────────────────────

    async def _get_via_sidecar(self, username: str) -> dict[str, Any] | None:
        """Use the existing aiograpi-rest sidecar in anonymous mode."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._sidecar_url}/user",
                    params={"username": username},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data and data.get("pk"):
                        return data
        except Exception as exc:
            logger.debug("Free IG sidecar fetch failed for %s: %s", username, exc)
        return None

    # ── Tier 2: HTML scraper ───────────────────────────────────────────────

    async def _get_via_html(self, username: str) -> dict[str, Any] | None:
        """Scrape instagram.com/{username}/ for og:description + embedded JSON."""
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(
                    _PROFILE_URL.format(username=username),
                    headers=_HTML_HEADERS,
                )
                if resp.status_code != 200:
                    logger.debug("Free IG HTML fetch returned HTTP %d for %s", resp.status_code, username)
                    return None
                html = resp.text

                og = _parse_og(html)
                embedded = _parse_embedded(html)
                og_image = _parse_og_image(html)
                meta_bio = _parse_meta_description(html)
                if og or embedded:
                    out: dict[str, Any] = {"username": username}
                    out.update(og or {})
                    out.update(embedded or {})
                    if og_image:
                        out["profile_pic_url"] = og_image
                    if meta_bio and not out.get("biography"):
                        out["biography"] = meta_bio
                    out["_source"] = "og+embedded" if (og and embedded) else ("og" if og else "embedded")
                    return out

                # Check for login wall
                head = html[:4096].lower()
                if any(marker in head for marker in _WALL_MARKERS):
                    logger.debug("Free IG HTML walled for %s", username)
                    return None
        except Exception as exc:
            logger.debug("Free IG HTML fetch failed for %s: %s", username, exc)
        return None

    # ── Public API ─────────────────────────────────────────────────────────

    async def get_user_by_username(self, username: str) -> dict[str, Any]:
        """Get a public Instagram profile. Tries sidecar first, then HTML."""
        # Tier 1: sidecar anonymous
        result = await self._get_via_sidecar(username)
        if result:
            result["_method"] = "sidecar_anon"
            return result

        # Tier 2: HTML scraper
        result = await self._get_via_html(username)
        if result:
            result["_method"] = "html_scraper"
            return result

        raise FreeInstagramError(
            f"Could not fetch Instagram profile for @{username} via any free method. "
            "The account may be private, or Instagram is blocking the request."
        )

    async def get_user_by_id(self, user_id: str | int) -> dict[str, Any]:
        """Get a public Instagram profile by numeric ID (sidecar only)."""
        result = await self._get_via_sidecar(str(user_id))
        if result:
            result["_method"] = "sidecar_anon"
            return result
        raise FreeInstagramError(f"Could not fetch Instagram user {user_id} via sidecar (HTML scraper needs username)")

    async def get_user_medias(self, username: str, amount: int = 12) -> list[dict[str, Any]]:
        """Get recent media posts (sidecar only — HTML scraper doesn't support this)."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._sidecar_url}/user/medias",
                    params={"username": username, "amount": amount},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    return data.get("medias", data.get("items", []))
        except Exception as exc:
            logger.debug("Free IG media fetch failed for %s: %s", username, exc)
        return []


# Singleton
free_instagram_client = FreeInstagramClient()
