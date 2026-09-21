"""Per-platform content adaptation.

Takes a Post + platform name and returns text that respects each platform's
character limits, hashtag caps, and link inclusion rules.

Uses the platform hints already defined in ``app.services.seo``.
"""
from __future__ import annotations

import re

from app.models.content import Post
from app.services.seo import _PLATFORM_HINTS

# Platforms where link URLs should be appended to the body text.
# Instagram and TikTok captions do not support clickable links, and the
# project's own SEO scoring penalises links in IG/TikTok captions.
_LINK_IN_BODY = {"linkedin", "facebook"}

# Platforms where hashtags are appended to the body text (vs. omitted or
# placed in a separate field).
_HASHTAG_IN_BODY = {"twitter", "instagram", "tiktok", "facebook", "linkedin"}


# Model artifact patterns seen in generated drafts — normalise before publish.
# {hashtag|\#|cloudless} → #cloudless ; [link to X] placeholder → dropped
_HASHTAG_MARKUP_RE = re.compile(r"\{hashtag\|\\#\|([^}]+)\}")
_PLACEHOLDER_LINK_RE = re.compile(r"\[link to [^\]]*\]", re.IGNORECASE)
_URN_ONLY_RE = re.compile(r"^urn:li:\w+:\d+$")


def sanitize_generated_text(text: str) -> str:
    """Strip model markup artifacts and placeholder links from generated text."""
    if not text:
        return text
    text = _HASHTAG_MARKUP_RE.sub(lambda m: f"#{m.group(1).strip()}", text)
    text = _PLACEHOLDER_LINK_RE.sub("", text)
    # Collapse horizontal whitespace runs left behind by removals
    text = re.sub(r"[^\S\n]+", " ", text)
    # Drop spaces before punctuation/newlines introduced by removals
    text = re.sub(r" +([,.!?;:\n])", r"\1", text)
    # Trim spaces after newlines; collapse 3+ newlines to a paragraph break
    text = re.sub(r"\n +", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_url(url: str) -> str:
    """Normalize a URL for comparison — scheme, www prefix, and trailing slash
    are stripped so ``www.cloudless.gr`` matches ``https://cloudless.gr/``."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def strip_embedded_metadata(content: str, hashtags: list[str] | None, link_url: str | None) -> str:
    """Remove trailing lines that duplicate dedicated post fields.

    ``render_post_text`` appends ``post.hashtags`` and ``post.link_url`` at
    publish time, so generated text that already ends with those values would
    print them twice. Strips, from the end of ``content`` only:

    * lines made solely of hashtags that are all present in ``hashtags``
    * a standalone line whose URL normalizes to ``link_url``

    Inline hashtags/URLs and trailing content not covered by the metadata
    fields are left untouched.
    """
    if not content:
        return content
    tagset = {h.lstrip("#").lower() for h in (hashtags or [])}
    link_norm = _normalize_url(link_url) if link_url else None
    lines = content.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        tokens = last.split()
        if tagset and all(t.startswith("#") for t in tokens) and all(
            t.lstrip("#").lower() in tagset for t in tokens
        ):
            lines.pop()
            continue
        if link_norm and len(tokens) == 1 and _normalize_url(tokens[0]) == link_norm:
            lines.pop()
            continue
        break
    stripped = "\n".join(lines).rstrip()
    return stripped if stripped else content.rstrip()


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
        parts.append(sanitize_generated_text(override["content_text"]))
    elif post.content_text:
        parts.append(sanitize_generated_text(post.content_text))

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
