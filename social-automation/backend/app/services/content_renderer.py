"""Per-platform content adaptation.

Takes a Post + platform name and returns text that respects each platform's
character limits, hashtag caps, and link inclusion rules.

Uses the platform hints already defined in ``app.services.seo``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.models.content import Post
from app.services.seo import _PLATFORM_HINTS

# Platforms where link URLs should be appended to the body text.
# Instagram and TikTok captions do not support clickable links, and the
# project's own SEO scoring penalises links in IG/TikTok captions.
_LINK_IN_BODY = {"linkedin", "facebook"}

# Platforms where hashtags are appended to the body text (vs. omitted or
# placed in a separate field).
_HASHTAG_IN_BODY = {"twitter", "instagram", "tiktok", "facebook", "linkedin"}


# Model artifact markers seen in generated drafts — normalise before publish.
# {hashtag|\#|cloudless} → #cloudless ; [link to X] placeholder → dropped
_HASHTAG_MARKUP_PREFIX = "{hashtag|\\#|"
_PLACEHOLDER_LINK_PREFIX = "[link to "


def _replace_hashtag_markup(text: str) -> str:
    """Replace ``{hashtag|\\#|X}`` artifacts with ``#X``.

    Plain ``str.find`` scan — linear on untrusted input where an equivalent
    regex with ``[^}]+`` can backtrack quadratically.
    """
    out: list[str] = []
    i = 0
    while (start := text.find(_HASHTAG_MARKUP_PREFIX, i)) != -1:
        end = text.find("}", start + len(_HASHTAG_MARKUP_PREFIX))
        if end == -1:
            break
        if end == start + len(_HASHTAG_MARKUP_PREFIX):
            # Empty markup — not an artifact, keep scanning after the '{'.
            out.append(text[i : start + 1])
            i = start + 1
            continue
        out.append(text[i:start])
        out.append(f"#{text[start + len(_HASHTAG_MARKUP_PREFIX) : end].strip()}")
        i = end + 1
    out.append(text[i:])
    return "".join(out)


def _drop_placeholder_links(text: str) -> str:
    """Drop ``[link to ...]`` placeholders (case-insensitive) — linear scan."""
    lower = text.lower()
    out: list[str] = []
    i = 0
    while (start := lower.find(_PLACEHOLDER_LINK_PREFIX, i)) != -1:
        end = text.find("]", start + len(_PLACEHOLDER_LINK_PREFIX))
        if end == -1:
            break
        out.append(text[i:start])
        i = end + 1
    out.append(text[i:])
    return "".join(out)


# Offer countdown token: ``{countdown}`` or ``{countdown:<ISO-8601 deadline>}``
# expands at render time (i.e. at publish) to a live "N days, H hours,
# M minutes" string — owner rule 2026-11-22: every Black Friday campaign post
# carries the real remaining time, never a static "this weekend".
# The bare form reads the deadline from ``post.meta_data["offer_ends_at"]``.
_COUNTDOWN_TOKEN = re.compile(r"\{countdown(?::([^}\s]+))?\}")


def _expand_countdown_tokens(
    text: str,
    *,
    now: datetime | None = None,
    meta: dict | None = None,
) -> str:
    """Expand countdown tokens to a live countdown.

    ``{countdown:<ISO-8601>}`` carries its own deadline; bare ``{countdown}``
    resolves ``meta["offer_ends_at"]``. A naive deadline is read as UTC, so
    authoring should carry an explicit offset (EET = +02:00, EEST = +03:00).
    An expired deadline renders "expired". A bare token with no meta deadline,
    or one that does not parse, is left in place rather than silently dropped,
    so a typo is visible in review instead of publishing missing copy.
    """
    if "{countdown" not in text:
        return text
    now = now or datetime.now(UTC)

    def _render(match: re.Match[str]) -> str:
        iso = match.group(1)
        if iso is None:
            iso = str((meta or {}).get("offer_ends_at") or "")
            if not iso:
                return match.group(0)
        try:
            deadline = datetime.fromisoformat(iso.strip())
        except ValueError:
            return match.group(0)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        delta = deadline - now
        if delta.total_seconds() <= 0:
            return "expired"
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        return f"{days} days, {hours} hours, {minutes} minutes"

    return _COUNTDOWN_TOKEN.sub(_render, text)


def sanitize_generated_text(text: str) -> str:
    """Strip model markup artifacts and placeholder links from generated text."""
    if not text:
        return text
    text = _replace_hashtag_markup(text)
    text = _drop_placeholder_links(text)
    # Collapse horizontal whitespace runs left behind by removals
    text = re.sub(r"[^\S\n]+", " ", text)
    # Drop spaces before punctuation/newlines introduced by removals
    # (runs were collapsed above, so a single space is all that can remain)
    text = re.sub(r" ([,.!?;:\n])", r"\1", text)
    # Trim spaces after newlines; collapse 3+ newlines to a paragraph break
    text = re.sub(r"\n +", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_hashtags(hashtags: list[str] | None) -> list[str]:
    """Canonical hashtag form: no ``#`` prefix, trimmed, deduped (case-insensitive).

    Posts store bare tag names (``"cloudless"``); renderers add the ``#``.
    Older write paths persisted the prefix itself, producing ``##cloudless``
    in the UI. Applies to every tag-like value entering a post field.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in hashtags or []:
        tag = str(raw).strip().lstrip("#").strip()
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    return out


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
        if tagset and all(t.startswith("#") for t in tokens) and all(t.lstrip("#").lower() in tagset for t in tokens):
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


def render_post_text(post: Post, platform: str, *, expand_countdown: bool = True) -> str:
    """Adapt ``post`` content for ``platform``.

    * Uses per-platform override from ``post.platform_specific`` if present.
    * Appends hashtags (capped to the platform's ideal range).
    * Appends link URL for platforms that support in-body links.
    * Truncates to the platform's max character limit.

    ``expand_countdown=False`` keeps ``{countdown}`` tokens raw — used when
    the rendered text is persisted for a later publish (cross-posting), so
    the countdown stays live until the actual send instead of freezing at
    adapt time.
    """
    parts: list[str] = []

    # Per-platform text override takes priority
    meta = getattr(post, "meta_data", None) or {}
    expand = _expand_countdown_tokens if expand_countdown else (lambda t, **_: t)
    override = (post.platform_specific or {}).get(platform, {})
    if override.get("content_text"):
        parts.append(expand(sanitize_generated_text(override["content_text"]), meta=meta))
    elif post.content_text:
        parts.append(expand(sanitize_generated_text(post.content_text), meta=meta))

    body = parts[0] if parts else ""

    # Hashtags — skip tags already embedded in the body. AI-generated copy
    # often includes its own hashtag line; appending again duplicates it.
    if post.hashtags and platform in _HASHTAG_IN_BODY:
        lo, hi = _ideal_hashtag_count(platform)
        tags = post.hashtags[:hi] if hi > 0 else []
        missing = [t for t in tags if not re.search(rf"#{re.escape(t.lstrip('#'))}\b", body, re.IGNORECASE)]
        if missing:
            tag_str = " ".join(f"#{t.lstrip('#')}" for t in missing)
            parts.append(tag_str)

    # Link URL — skip when the body already links to the same host. The
    # generator frequently ends copy with the bare domain, so compare on
    # the host rather than the exact URL string.
    if post.link_url and platform in _LINK_IN_BODY:
        if not _link_already_present(body, post.link_url):
            parts.append(post.link_url)

    text = "\n\n".join(p for p in parts if p)

    # Truncate to platform max (leave room for ellipsis). If the cut lands
    # inside a {countdown} token, drop back to before it — a truncated token
    # can't be expanded at publish and would ship as literal garbage.
    # Literal braces (JSON-style bodies) are ordinary content and stay.
    max_len = _max_chars(platform)
    if len(text) > max_len:
        cut = text[: max_len - 1]
        brace = cut.rfind("{")
        frag = cut[brace:] if brace != -1 else ""
        if frag and "}" not in frag and ("{countdown".startswith(frag) or frag.startswith("{countdown")):
            cut = cut[:brace]
        text = cut.rstrip() + "…"

    return text


def _link_already_present(body: str, link_url: str) -> bool:
    """True when ``body`` already links to ``link_url``'s host.

    Normalises scheme + ``www.`` so ``https://cloudless.gr/x`` matches a
    body that ends with ``www.cloudless.gr/x`` or ``cloudless.gr/x``.
    """
    if not link_url or not body:
        return False

    def _norm(url: str) -> str:
        u = url.strip().lower()
        u = re.sub(r"^https?://", "", u)
        u = re.sub(r"^www\.", "", u)
        return u.rstrip("/")

    host = _norm(link_url).split("/", 1)[0]
    if not host or "." not in host:
        return False
    # Normalise the body the same way — strip mid-string schemes and a
    # standalone "www." so "www.cloudless.gr" compares as "cloudless.gr".
    body_norm = _norm(body)
    body_norm = re.sub(r"https?://", "", body_norm)
    body_norm = re.sub(r"(?<![\w])www\.", "", body_norm)
    # Match host+path first, then the bare host — e.g. body
    # "...cloudless.gr/pricing" hits link "cloudless.gr/pricing", and body
    # "...cloudless.gr" hits either form. Boundary anchors keep
    # "mycloudless.gr" or "sub.cloudless.gr" from counting as the same link.
    path = _norm(link_url)
    for needle in {path, host}:
        if re.search(rf"(?<![\w.-]){re.escape(needle)}(?![\w-])", body_norm):
            return True
    return False


def render_hashtags(post: Post, platform: str) -> list[str]:
    """Return the hashtag list capped to the platform's ideal count."""
    if not post.hashtags:
        return []
    _, hi = _ideal_hashtag_count(platform)
    if hi == 0:
        return []
    return post.hashtags[:hi]
