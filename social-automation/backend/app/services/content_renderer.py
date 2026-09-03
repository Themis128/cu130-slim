"""Per-platform content adaptation.

Takes a Post + platform name and returns text that respects each platform's
character limits, hashtag caps, and link inclusion rules.

Uses the platform hints already defined in ``app.services.seo``.
"""
from __future__ import annotations

from app.models.content import Post
from app.services.seo import _PLATFORM_HINTS

# Platforms where link URLs should be appended to the body text.
# Instagram and TikTok captions do not support clickable links, and the
# project's own SEO scoring penalises links in IG/TikTok captions.
_LINK_IN_BODY = {"linkedin", "facebook"}

# Platforms where hashtags are appended to the body text (vs. omitted or
# placed in a separate field).
_HASHTAG_IN_BODY = {"twitter", "instagram", "tiktok", "facebook", "linkedin"}


def _ideal_hashtag_count(platform: str) -> tuple[int, int]:
    hint = _PLATFORM_HINTS.get(platform, {})
    val = hint.get("ideal_hashtags")
    if isinstance(val, tuple) and len(val) == 2:
        return val
    return (0, 5)


def _max_chars(platform: str) -> int:
    hint = _PLATFORM_HINTS.get(platform, {})
    mc = hint.get("max_chars")
    return int(mc) if isinstance(mc, int) else 3000


def render_post_text(post: Post, platform: str) -> str:
    """Adapt ``post`` content for ``platform``.

    * Uses per-platform override from ``post.platform_specific`` if present.
    * Appends hashtags (capped to the platform's ideal range).
    * Appends link URL for platforms that support in-body links.
    * Truncates to the platform's max character limit.
    """
    parts: list[str] = []

    # Per-platform text override takes priority
    override = (post.platform_specific or {}).get(platform, {})
    if override.get("content_text"):
        parts.append(override["content_text"])
    elif post.content_text:
        parts.append(post.content_text)

    # Hashtags
    if post.hashtags and platform in _HASHTAG_IN_BODY:
        lo, hi = _ideal_hashtag_count(platform)
        tags = post.hashtags[:hi] if hi > 0 else []
        if tags:
            tag_str = " ".join(f"#{t.lstrip('#')}" for t in tags)
            parts.append(tag_str)

    # Link URL
    if post.link_url and platform in _LINK_IN_BODY:
        parts.append(post.link_url)

    text = "\n\n".join(p for p in parts if p)

    # Truncate to platform max (leave room for ellipsis)
    max_len = _max_chars(platform)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"

    return text


def render_hashtags(post: Post, platform: str) -> list[str]:
    """Return the hashtag list capped to the platform's ideal count."""
    if not post.hashtags:
        return []
    _, hi = _ideal_hashtag_count(platform)
    if hi == 0:
        return []
    return post.hashtags[:hi]
