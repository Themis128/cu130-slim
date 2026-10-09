"""Social media publishing pipeline.

Platform capabilities actually used per connected account:
  Twitter  (@TBaltzakis)           — text + images (up to 4) w/ alt text, GIF, video (v2 chunked), threads
  Facebook (personal user token)   — pages managed by user: text + photos + multi-photo
  Instagram (Business/Creator)     — single image + carousel (requires public image URLs)
  LinkedIn  (person + org page)    — text / single image / multi-image / PDF carousel
  Threads                        — text + single image (via Threads Content Publishing API)
  TikTok                         — video + photo carousel (via inbox upload, PULL_FROM_URL)

Platform driver abstraction: ``app.services.platforms`` provides a uniform
``PlatformDriver`` protocol with ``publish()``, ``delete()``, and
``get_follower_count()`` methods per platform.
Content adaptation: ``app.services.content_renderer`` handles per-platform
text truncation, hashtag caps, and link inclusion rules.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import secrets
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_field, decrypt_token
from app.models.content import MediaAsset, Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.services.duplicate_detector import is_duplicate
from app.services.facebook_api import FacebookAPIClient
from app.services.facebook_sidecar import FacebookSidecarClient, FacebookSidecarError
from app.services.instagram_api import InstagramAPIClient, InstagramAPIError
from app.services.instagram_private_api import (
    InstagramPrivateAPIClient,
    InstagramPrivateAPIError,
)
from app.services.instagrapi_client import InstagrapiClient, InstagrapiError
from app.services.linkedin_api import LinkedInAPIClient, LinkedInAPIError
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError
from app.services.meta_graph import FACEBOOK_GRAPH_BASE, FACEBOOK_GRAPH_VERSION, facebook_graph_url
from app.services.spellcheck import auto_correct
from app.services.threads_api import ThreadsAPIClient, ThreadsAPIError
from app.services.tiktok_api import TikTokAPIClient, TikTokAPIError
from app.services.twitter_api import TwitterAPIClient, TwitterAPIError

logger = logging.getLogger(__name__)

_settings = get_settings()


@dataclasses.dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None
    skipped: bool = False
    # Merged into Post.platform_specific by the publishing worker (e.g. TikTok
    # publish_id kept alongside the public Display API video id).
    platform_meta: dict[str, Any] | None = None
    # True when the failure happened at/after the platform's publish boundary
    # and the post may actually be live despite the error (e.g. Instagram
    # 2207051 "Application request limit reached" — a documented false
    # negative). The queue worker schedules a delayed feed reconciliation
    # for these instead of leaving the target failed forever.
    ambiguous: bool = False
    # Soft deferral (e.g. X web fallback daily cap / min gap): the worker
    # re-queues the item for this time without burning an attempt.
    retry_after: datetime | None = None
    # Deterministic failure (invalid media, tripped safety breaker, wrong
    # account): retrying cannot help — fail now and alert once.
    permanent: bool = False




# Platform-limit signatures → soft-skip (do not burn retries / reconnect).
_FB_GROUP_MARKERS = (
    "posting to a group",
    "publish_to_groups",
    "app being installed in the group",
)
_TT_OWNERSHIP_MARKERS = ("url_ownership_unverified",)
_TT_UNAUDITED_MARKERS = ("unaudited_client_can_only_post_to_private_accounts",)
_X_QUOTA_MARKERS = (
    "credits-depleted",
    "usagecapexceeded",
    "usage-capped",
    "rate-limit-exceeded",
    "too many requests",
    "monthly write",
    "tweet cap",
    "free tier",
)


def _err_has(text: str | None, markers: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(m.lower() in low for m in markers)


def _is_x_quota_error(exc: TwitterAPIError) -> bool:
    """402 credits-depleted / usage-capped / 429 rate-limit from the X API.

    X documents 429 for both ``.../rate-limit-exceeded`` (short window) and
    ``.../usage-capped`` (billing cap); 402 is returned for depleted
    pay-per-use credits. All are capacity problems, never content problems.
    """
    blob = f"{exc} {getattr(exc, 'response_text', '')}"
    return exc.status_code in (402, 429) or _err_has(blob, _X_QUOTA_MARKERS)


def _x_capacity_backoff() -> datetime:
    minutes = max(1, int(getattr(_settings, "X_CAPACITY_BACKOFF_MINUTES", 60) or 60))
    return datetime.now(UTC) + timedelta(minutes=minutes)


def _x_retry_at(exc: TwitterAPIError | None) -> datetime | None:
    """When X API write capacity should return, from the documented headers.

    - ``x-rate-limit-reset`` (unix seconds): the endpoint's 15-min window —
      only meaningful for ``rate-limit-exceeded``, not for usage caps.
    - ``x-user-limit-24hour-reset`` / ``x-app-limit-24hour-reset``: the 24h
      POST /2/tweets caps, used when their ``-remaining`` is 0.
    - ``retry-after`` (seconds).
    Credits-depleted (402) / usage-capped carry no reset time → ``None``;
    callers then use the X_CAPACITY_BACKOFF_MINUTES backoff (or the fallback's
    own slot time) so a top-up / newly enabled fallback is picked up without
    hammering the API.
    """
    now = datetime.now(UTC)
    if exc is None:
        return None
    hdrs: dict[str, str] = getattr(exc, "headers", None) or {}
    blob = f"{exc} {getattr(exc, 'response_text', '')}".lower()
    candidates: list[float] = []
    for scope in ("user", "app"):
        reset = hdrs.get(f"x-{scope}-limit-24hour-reset")
        remaining = hdrs.get(f"x-{scope}-limit-24hour-remaining")
        if reset and (remaining is None or remaining.strip() == "0"):
            try:
                candidates.append(float(reset))
            except ValueError:
                pass
    capped = exc.status_code == 402 or _err_has(blob, ("usage-capped", "usagecapexceeded", "credits-depleted"))
    if not capped:
        reset = hdrs.get("x-rate-limit-reset")
        if reset:
            try:
                candidates.append(float(reset))
            except ValueError:
                pass
        ra = hdrs.get("retry-after")
        if ra:
            try:
                candidates.append(now.timestamp() + float(ra))
            except ValueError:
                pass
    if not candidates:
        return None
    when = datetime.fromtimestamp(max(candidates), UTC) + timedelta(seconds=30)
    # Guard against clock skew / bogus headers: at least 1 min, at most 7 days.
    return min(max(when, now + timedelta(minutes=1)), now + timedelta(days=7))


def _facebook_group_skip_result(detail: str | None = None) -> PublishResult:
    """Meta removed Groups API (incl. publish_to_groups) in Graph v19+.

    https://developers.facebook.com/blog/post/2024/01/23/introducing-facebook-graph-and-marketing-api-v19/
    Page publishing still needs pages_manage_posts + pages_read_engagement.
    """
    extra = f" Upstream: {detail[:240]}" if detail else ""
    return PublishResult(
        success=False,
        skipped=True,
        error=(
            "Facebook Groups API is deprecated — Graph cannot publish to groups "
            "(publish_to_groups removed). Retarget to a Facebook Page with "
            "pages_manage_posts + pages_read_engagement, or use the personal "
            "browser sidecar for profile posts. Reconnect will not restore group "
            f"publishing.{extra}"
        ),
    )


def _tiktok_clarify_error(exc: Exception | str) -> str:
    """Map TikTok Content Posting error codes to actionable guidance."""
    raw = str(exc)
    if _err_has(raw, _TT_OWNERSHIP_MARKERS):
        return (
            "TikTok url_ownership_unverified: PULL_FROM_URL requires a verified "
            "Domain/URL Prefix in TikTok Developer Console (Media Transfer Guide). "
            "Prefer FILE_UPLOAD when a local video exists (already attempted if path "
            "was available), or verify MEDIA_PUBLIC_BASE_URL domain. Photos always "
            "need PULL_FROM_URL + verified ownership. "
            "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide"
        )
    if _err_has(raw, _TT_UNAUDITED_MARKERS):
        return (
            "TikTok unaudited_client_can_only_post_to_private_accounts: Direct Post "
            "to public/friends privacy needs App Review audit. Until audited, use "
            "publish_mode=MEDIA_UPLOAD (inbox draft) or DIRECT_POST with SELF_ONLY "
            "on a private account. "
            "https://developers.tiktok.com/doc/content-posting-api-reference-direct-post"
        )
    return raw


def _x_quota_skip_result(detail: str | None = None) -> PublishResult:
    """Browser-bridge "unavailable" marker (``skipped=True``).

    Only consumed inside ``_publish_twitter_fallbacks``, which turns it into a
    capacity *deferral* — it never reaches the worker as a terminal skip.
    """
    extra = f" Detail: {detail[:240]}" if detail else ""
    return PublishResult(
        success=False,
        skipped=True,
        error=(
            "X free tier monthly write quota / API credits exhausted and the "
            f"browser-bridge fallback is unavailable.{extra}"
        ),
    )


def _x_capacity_defer_result(detail: str | None = None, retry_at: datetime | None = None) -> PublishResult:
    """X write capacity exhausted (API credits/cap, no fallback slot) — DEFER.

    The target stays pending and the queue row is re-scheduled for
    ``retry_after`` (the worker bounds total deferral age/count). Before
    2026-10-04 this was a soft *skip*, which completed the queue row and
    left posts stuck forever once a fallback became available.
    """
    extra = f" Detail: {detail[:240]}" if detail else ""
    return PublishResult(
        success=False,
        retry_after=retry_at or _x_capacity_backoff(),
        error=(
            "X write capacity unavailable (API credits / quota exhausted and no "
            "free fallback slot) — deferred, will retry automatically. Reconnect "
            "will not fix this — add credits at console.x.com or ensure the X web "
            f"fallback / browser-bridge X session is logged in.{extra}"
        ),
    )


# ── media pre-flight (safety net) ────────────────────────────────────────────
# Background: the 2026-10-04 digest showed distorted AI-generated assets
# reaching production feeds (FLUX-generation settings sent to the local
# SD 1.5 model — fixed in #301) and IG targets burning retries on text-only
# posts. This pre-flight runs BEFORE any platform dispatch and fails soft
# (skipped=True) so the queue records a clear reason instead of shipping
# broken media or burning attempts.
#
# Owner rule: NEVER publish with missing or mismatched media. A post that
# references media assets which could not all be resolved is skipped — it
# must not silently degrade to a text-only post on platforms that allow text.
#
# Limits below come from the platforms' official docs (re-checked 2026-10-04):
#   instagram  (Graph API IG User Media): images JPEG, ≤8MB, aspect 4:5–1.91:1,
#              width 320–1440px (height varies with width/aspect — width is the
#              capped axis); Reels MP4/MOV ≤300MB; carousel ≤10 items
#   threads    (Threads API overview): images JPEG/PNG, ≤8MB, aspect ≤10:1,
#              width 320–1440px (height varies); video ≤1GB; carousel ≤20
#   facebook   (Graph API Page Photos / Video API): photos ≤10MB; video ≤2GB
#   twitter    (X media best-practices): images ≤5MB; post video ≤8GB
#              (default accounts); ≤4 media per post
#   linkedin   (Documents API / Videos API): documents (PDF) ≤100MB and ≤300
#              pages; video 75KB–500MB; images ≤6024px
#   tiktok     (Content Posting API): single video ≤4GB OR photo carousel ≤35
# Image byte caps (``max_bytes``) apply to stills only; videos are checked
# against ``max_video_bytes``. Keep this table and
# notebooks/media_validation.ipynb in sync.

MIN_IMAGE_BYTES = 10 * 1024   # below this it's a placeholder or truncated file
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXTS_ALL = (".mp4", ".mov", ".webm", ".avi")
_MB = 1024 * 1024
_GB = 1024 * _MB

_PLATFORM_MEDIA_RULES: dict[str, dict[str, Any]] = {
    # required: owner rule 2026-10-05 — "all posts must be with the correct
    #           media, never post without media" → empty media = skip on EVERY
    #           platform, not just the ones whose APIs reject text-only.
    # dim_axis: "width" → min/max_dim bound the width only (Meta docs cap the
    #           width; height is bounded by the aspect ratio); "both" → each side.
    "instagram": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png") + (".mp4", ".mov"),
        "min_dim": 320, "max_dim": 1440, "dim_axis": "width",
        "max_bytes": 8 * _MB, "max_video_bytes": 300 * _MB,
        "ratio_min": 4 / 5, "ratio_max": 1.91, "max_count": 10,
    },
    "tiktok": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png", ".webp") + VIDEO_EXTS_ALL,
        "min_dim": 360, "max_dim": 4096, "dim_axis": "both",
        "max_bytes": 1 * _GB, "max_video_bytes": 4 * _GB,
        "ratio_min": None, "ratio_max": None, "max_count": 35,
    },
    "linkedin": {
        "required": True, "allow_pdf": True, "max_pdf_bytes": 100 * _MB,
        "formats": (".jpg", ".jpeg", ".png", ".gif") + VIDEO_EXTS_ALL,
        "min_dim": 200, "max_dim": 6024, "dim_axis": "both",
        "max_bytes": 100 * _MB, "max_video_bytes": 500 * _MB,
        "ratio_min": None, "ratio_max": None, "max_count": 20,
    },
    "facebook": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png", ".gif") + VIDEO_EXTS_ALL,
        "min_dim": 200, "max_dim": 4096, "dim_axis": "both",
        "max_bytes": 10 * _MB, "max_video_bytes": 2 * _GB,
        "ratio_min": None, "ratio_max": None, "max_count": 10,
    },
    "twitter": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png", ".webp") + VIDEO_EXTS_ALL,
        "min_dim": 200, "max_dim": 8192, "dim_axis": "both",
        "max_bytes": 5 * _MB, "max_video_bytes": 8 * _GB,
        "ratio_min": None, "ratio_max": None, "max_count": 4,
    },
    "threads": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png", ".gif") + VIDEO_EXTS_ALL,
        "min_dim": 320, "max_dim": 1440, "dim_axis": "width",
        "max_bytes": 8 * _MB, "max_video_bytes": 1 * _GB,
        "ratio_min": 1 / 10, "ratio_max": 10.0, "max_count": 20,
    },
    # AT Protocol lexicon caps: 4 images ≤2MB each, single mp4 ≤300MB.
    # Videos: embed.video accepts video/mp4 only.
    "bluesky": {
        "required": True, "allow_pdf": False,
        "formats": (".jpg", ".jpeg", ".png", ".webp", ".gif") + (".mp4",),
        "min_dim": 200, "max_dim": 4096, "dim_axis": "both",
        "max_bytes": 2 * _MB, "max_video_bytes": 300 * _MB,
        "ratio_min": None, "ratio_max": None, "max_count": 4,
    },
}


def _skipped_media_result(reason: str) -> PublishResult:
    return PublishResult(success=False, skipped=True, error=reason)


def _fmt_bytes(n: int) -> str:
    return f"{n // _GB}GB" if n >= _GB and n % _GB == 0 else f"{n // _MB}MB"


def validate_media_for_platform(
    platform: str, media_paths: list[str], expected: int = 0
) -> PublishResult | None:
    """Pre-flight every media file against the target platform's constraints.

    ``expected`` is the number of distinct media assets the post references
    (``post.media_ids``). When fewer local paths resolved than expected, the
    post is skipped — it must never publish text-only or with a partial
    media set (owner rule: no missing/mismatched media).

    Returns a ``skipped`` PublishResult when the media cannot publish
    correctly on ``platform`` (missing/unresolvable media, corrupt or
    unparseable file, dimensions, size or type out of bounds), or ``None``
    when publishing may proceed. Deterministic problems only — this never
    blocks on content quality; use the media-notebooks QA flow (DMR VLM
    caption match) for semantic checks.
    """
    rules = _PLATFORM_MEDIA_RULES.get(platform)
    if rules is None:
        return None  # unknown platform — let the dispatcher handle it

    plat = platform.capitalize()

    if expected and len(media_paths) < expected:
        return _skipped_media_result(
            f"Post media incomplete: only {len(media_paths)}/{expected} media "
            "assets could be resolved from storage — refusing to publish with "
            "missing media. Re-upload the asset(s) or fix the post's media."
        )

    if not media_paths:
        if rules["required"]:
            return _skipped_media_result(
                f"{plat} post has no media — every post must carry at least "
                "one image/video (owner policy: never post without media). "
                "Attach media or remove the target."
            )
        return None  # genuinely text-only post (no media referenced)

    if len(media_paths) > rules["max_count"]:
        return _skipped_media_result(
            f"{plat} accepts at most {rules['max_count']} media "
            f"items per post — got {len(media_paths)}. Reduce the media count."
        )

    for path in media_paths:
        lower = path.lower()
        name = os.path.basename(path)
        is_pdf = lower.endswith(".pdf")

        if is_pdf and not rules["allow_pdf"]:
            return _skipped_media_result(
                f"{plat} does not accept PDF media ({name}). Attach an "
                "image/video or remove the target."
            )

        # Existence + size checks apply to EVERY media type (images, videos,
        # PDFs) — a missing or empty file must never soft-pass.
        if not os.path.exists(path):
            return _skipped_media_result(
                f"Media file missing from storage: {name} — "
                "re-upload the asset or remove the target."
            )
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            return _skipped_media_result(
                f"Media file unreadable ({exc}) — re-upload the asset."
            )

        if is_pdf:
            # LinkedIn document posts: ≤100MB, ≤300 pages (Documents API).
            if size == 0:
                return _skipped_media_result(
                    f"PDF {name} is empty (0 bytes) — re-upload the document."
                )
            max_pdf = rules.get("max_pdf_bytes") or rules["max_bytes"]
            if size > max_pdf:
                return _skipped_media_result(
                    f"PDF {name} is {size // 1024}KB — above {plat}'s document "
                    f"limit ({_fmt_bytes(max_pdf)}). Compress or split it."
                )
            try:
                with open(path, "rb") as fh:
                    head = fh.read(5)
            except OSError as exc:
                return _skipped_media_result(
                    f"PDF {name} unreadable ({exc}) — re-upload the document."
                )
            if head != b"%PDF-":
                return _skipped_media_result(
                    f"{name} is not a valid PDF (bad header) — re-export the document."
                )
            continue

        if size < MIN_IMAGE_BYTES:
            return _skipped_media_result(
                f"Media file {name} is only {size} bytes — "
                "likely a truncated or placeholder upload. Re-upload the asset."
            )

        if lower.endswith(VIDEO_EXTS_ALL):
            # Videos have their own (much larger) byte caps; the image
            # ``max_bytes`` must NOT apply here or Reels/videos get skipped.
            if not lower.endswith(rules["formats"]):
                return _skipped_media_result(
                    f"Media {name} has a format {plat} does not accept "
                    f"(allowed: {', '.join(rules['formats'])}). Re-export the asset."
                )
            if size > rules["max_video_bytes"]:
                return _skipped_media_result(
                    f"Video {name} is {size // _MB}MB — above {plat}'s video "
                    f"limit ({_fmt_bytes(rules['max_video_bytes'])}). Compress or trim."
                )
            # fps/duration/codec are validated by the dedicated paths
            # (validate_tiktok_video_constraints, browser sidecars).
            continue

        if size > rules["max_bytes"]:
            return _skipped_media_result(
                f"Media {name} is {size // 1024}KB — above {plat}'s image "
                f"limit ({_fmt_bytes(rules['max_bytes'])}). Compress or resize."
            )

        try:
            from PIL import Image

            with Image.open(path) as im:
                im.verify()  # catches truncation/corruption without a full decode
            with Image.open(path) as im:
                width, height = im.size
        except Exception as exc:  # noqa: BLE001 — any parse failure is a bad asset
            return _skipped_media_result(
                f"Media file {name} is not a decodable image "
                f"({type(exc).__name__}) — re-upload the asset."
            )

        width_only = rules.get("dim_axis") == "width"
        too_small = (
            width < rules["min_dim"]
            if width_only
            else (width < rules["min_dim"] or height < rules["min_dim"])
        )
        if too_small:
            what = "width" if width_only else "minimum"
            return _skipped_media_result(
                f"Media {name} is {width}x{height}px — below "
                f"{plat}'s {what} {rules['min_dim']}px. Upload a larger rendition."
            )
        too_big = (
            width > rules["max_dim"]
            if width_only
            else (width > rules["max_dim"] or height > rules["max_dim"])
        )
        if too_big:
            what = "max width" if width_only else "limit"
            return _skipped_media_result(
                f"Media {name} is {width}x{height}px — above "
                f"{plat}'s {what} {rules['max_dim']}px. Downscale the rendition."
            )
        ratio = width / height
        if (rules["ratio_min"] and ratio < rules["ratio_min"]) or (
            rules["ratio_max"] and ratio > rules["ratio_max"]
        ):
            return _skipped_media_result(
                f"Media {name} is {width}x{height}px — {plat} "
                f"requires aspect ratio between {rules['ratio_min']:.2f} and "
                f"{rules['ratio_max']:.2f} (width/height). Crop or pad the image."
            )
        if not lower.endswith(rules["formats"]):
            return _skipped_media_result(
                f"Media {name} has a format {plat} does not accept "
                f"(allowed: {', '.join(rules['formats'])}). Re-export the asset."
            )
    return None


# ── entry point ───────────────────────────────────────────────────────────────

async def publish_to_platform(
    account: SocialAccount,
    post: Post,
    db: AsyncSession,
) -> PublishResult:
    # WhatsApp is a messaging channel (Cloud API), not a feed-style social platform.
    # If a post target includes WhatsApp (e.g. legacy UI or automation), skip it so
    # other platforms can still publish successfully.
    if account.platform in ("whatsapp", "telegram", "viber"):
        channel = {"whatsapp": "WhatsApp", "telegram": "Telegram", "viber": "Viber"}[
            account.platform
        ]
        return PublishResult(
            success=False,
            skipped=True,
            error=(
                f"{channel} is a messaging channel in SocialAuto and does not support feed-style post publishing. "
                f"Use the {channel} send endpoints instead."
            ),
        )

    try:
        raw_enc = bytes(account.access_token_enc) if not isinstance(account.access_token_enc, bytes) else account.access_token_enc
        access_token = decrypt_token(raw_enc)
    except Exception as exc:
        return PublishResult(success=False, error=f"Token decrypt failed: {exc}")

    text = _build_post_text(post, account.platform)
    # Spellcheck the final assembled text (including hashtags, links, platform overrides)
    try:
        corrected = await auto_correct(text)
        if corrected:
            text = corrected
    except Exception:
        pass  # spellcheck is advisory — never block publishing

    # Empty-copy guard (owner rule: every post must carry copy that went
    # through the quality pipeline). A post with no text, no override, no
    # hashtags and no link publishes a dead caption — fail loudly so it
    # surfaces in the digest instead of shipping an empty Instagram caption.
    if not text.strip():
        return PublishResult(
            success=False,
            error=(
                "Post has no text content (empty content_text, no platform "
                "override, no hashtags, no link) — refusing to publish an "
                "empty caption. Add copy or generate content first."
            ),
        )

    # No-duplicates guard (owner rule 2026-10-02): refuse to publish a post
    # whose text or media matches something this account already published
    # in the last 30 days — catches retry/draft accidents and same-story
    # repackaging before it reaches the platform.
    dup = await _find_duplicate_post(db, post, account, text)
    if dup is not None:
        return PublishResult(
            success=False,
            skipped=True,
            error=(
                f"Duplicate content: matches published post {str(dup.id)[:8]} "
                "from the last 30 days — refusing to re-publish."
            ),
        )

    media_paths = await _resolve_media_paths(post, db)
    storage_paths = await _resolve_media_storage_paths(post, db)

    # Media pre-flight (safety net): never dispatch to a platform with media
    # that cannot publish correctly. Failures here are deterministic content/
    # config gaps — skipped, not retried.
    # ``expected`` = distinct assets referenced by the post: _resolve_media_paths
    # silently drops assets it cannot find locally or fetch from R2/MinIO, so
    # without it a post whose media failed to resolve would publish text-only.
    expected_media = len(dict.fromkeys(str(m) for m in (getattr(post, "media_ids", None) or [])))
    media_check = validate_media_for_platform(
        account.platform, media_paths, expected=expected_media
    )
    if media_check is not None:
        return media_check

    # If the post has a music asset and a single video, mix the audio in
    music_path = await _resolve_music_path(post, db)
    if music_path and media_paths and len(media_paths) == 1:
        vpath = media_paths[0]
        if vpath.lower().endswith((".mp4", ".mov", ".webm")):
            mixed = await _mix_audio_into_video(vpath, music_path)
            if mixed != vpath:
                media_paths[0] = mixed

    dispatch: dict[str, Any] = {
        "twitter": _publish_twitter,
        "linkedin": _publish_linkedin,
        "facebook": _publish_facebook,
        "instagram": _publish_instagram,
        "threads": _publish_threads,
        "tiktok": _publish_tiktok,
        "bluesky": _publish_bluesky,
    }
    fn = dispatch.get(account.platform)
    if fn is None:
        return PublishResult(success=False, error=f"Unsupported platform: {account.platform}")

    try:
        if account.platform in ("instagram", "twitter"):
            return await fn(access_token, text, account, post, media_paths, storage_paths, db)
        return await fn(access_token, text, account, post, media_paths, storage_paths)
    except httpx.HTTPStatusError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:400]}")
    except LinkedInAPIError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.status_code}: {exc.response_text[:400]}")
    except TikTokAPIError as exc:
        clarified = _tiktok_clarify_error(exc)
        skip = _err_has(clarified, _TT_OWNERSHIP_MARKERS + _TT_UNAUDITED_MARKERS)
        return PublishResult(success=False, skipped=skip, error=clarified)
    except TwitterAPIError as exc:
        blob = f"{exc} {getattr(exc, 'response_text', '')}"
        if _is_x_quota_error(exc):
            return _x_capacity_defer_result(blob, _x_retry_at(exc))
        return PublishResult(success=False, error=str(exc)[:500])
    except Exception as exc:
        blob = str(exc)
        if _err_has(blob, _FB_GROUP_MARKERS):
            return _facebook_group_skip_result(blob)
        if _err_has(blob, _TT_OWNERSHIP_MARKERS + _TT_UNAUDITED_MARKERS):
            return PublishResult(success=False, skipped=True, error=_tiktok_clarify_error(blob))
        return PublishResult(success=False, error=blob)


# ── helpers ───────────────────────────────────────────────────────────────────

_DUP_WINDOW_DAYS = 30
_DUP_TEXT_THRESHOLD = 0.55  # stricter than the field-level default (0.48)


async def _find_duplicate_post(
    db: AsyncSession,
    post: Post,
    account: SocialAccount,
    text: str,
) -> Post | None:
    """Return a recently published post to this account that duplicates the
    candidate — same media asset or near-identical caption text."""
    since = datetime.now(UTC) - timedelta(days=_DUP_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(Post)
            .join(PostTarget, PostTarget.post_id == Post.id)
            .where(
                PostTarget.social_account_id == account.id,
                Post.status == PostStatus.PUBLISHED,
                Post.id != post.id,
                Post.published_at >= since,
            )
        )
    ).scalars().all()

    media_set = {str(m) for m in (post.media_ids or [])}
    for other in rows:
        if media_set and media_set & {str(m) for m in (other.media_ids or [])}:
            return other
        if is_duplicate(text, _build_post_text(other, account.platform), threshold=_DUP_TEXT_THRESHOLD):
            return other
    return None


async def _resolve_media_paths(post: Post, db: AsyncSession) -> list[str]:
    if not post.media_ids:
        return []
    result = await db.execute(select(MediaAsset).where(MediaAsset.id.in_(post.media_ids)))
    assets = result.scalars().all()
    id_order = {str(mid): i for i, mid in enumerate(post.media_ids)}
    assets_sorted = sorted(assets, key=lambda a: id_order.get(str(a.id), 999))
    upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
    paths: list[str] = []
    for asset in assets_sorted:
        path = asset.storage_path
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.join(upload_dir, path)
        if os.path.exists(path):
            paths.append(path)
            continue
        # File not on local disk — try fetching from R2 or MinIO
        # and cache to a temp file for the publishing pipeline.
        backend = (asset.storage_backend or "").lower()
        data: bytes | None = None
        try:
            if backend == "r2":
                from app.services import r2_storage
                data = await r2_storage.get_object(asset.storage_path)
            elif backend == "minio":
                from app.services import minio_storage
                data = await minio_storage.get_object(asset.storage_path)
        except Exception as exc:
            logger.warning(f"[publishing] Failed to fetch {asset.storage_path} from {backend}: {exc}")
        if data:
            import tempfile
            ext = os.path.splitext(asset.filename or asset.storage_path or "")[1] or ".bin"
            # Save to the uploads directory so the file is accessible by
            # sidecar containers (instagram-private-api, etc.) that mount
            # the uploads volume at /uploads.
            upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=upload_dir)
            tmp.write(data)
            tmp.close()
            paths.append(tmp.name)
    return paths


async def _resolve_media_storage_paths(post: Post, db: AsyncSession) -> list[str]:
    """Return the original storage_paths for the post's media assets.

    Used by platforms that need public URLs (Instagram, Threads, TikTok)
    rather than local file paths. The storage_path works with
    _media_public_url() to build a URL the platform can fetch from the
    /api/v1/media/view endpoint, which transparently serves from R2,
    MinIO, or local disk.
    """
    if not post.media_ids:
        return []
    result = await db.execute(select(MediaAsset).where(MediaAsset.id.in_(post.media_ids)))
    assets = result.scalars().all()
    id_order = {str(mid): i for i, mid in enumerate(post.media_ids)}
    assets_sorted = sorted(assets, key=lambda a: id_order.get(str(a.id), 999))
    return [a.storage_path for a in assets_sorted if a.storage_path]


async def _resolve_music_path(post: Post, db: AsyncSession) -> str | None:
    """Resolve the file path for the post's music asset, if any."""
    if not post.music_asset_id:
        return None
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == post.music_asset_id))
    asset = result.scalar_one_or_none()
    if not asset or not asset.storage_path:
        return None
    upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
    path = asset.storage_path
    if not os.path.isabs(path):
        path = os.path.join(upload_dir, path)
    if os.path.exists(path):
        return path
    # File not on local disk — fetch from R2 or MinIO to a temp file.
    backend = (asset.storage_backend or "").lower()
    data: bytes | None = None
    try:
        if backend == "r2":
            from app.services import r2_storage
            data = await r2_storage.get_object(asset.storage_path)
        elif backend == "minio":
            from app.services import minio_storage
            data = await minio_storage.get_object(asset.storage_path)
    except Exception as exc:
        logger.warning(f"[publishing] Failed to fetch music {asset.storage_path} from {backend}: {exc}")
    if data:
        import tempfile
        ext = os.path.splitext(asset.filename or asset.storage_path or "")[1] or ".bin"
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir="/tmp")
        tmp.write(data)
        tmp.close()
        return tmp.name
    return None


async def _mix_audio_into_video(video_path: str, audio_path: str) -> str:
    """Mix an audio track into a video file using ffmpeg.

    Returns the path to the new video file with the mixed audio.
    The original video audio is replaced (not merged) with the music track.
    """
    import asyncio as _asyncio
    import shutil
    import tempfile

    if not shutil.which("ffmpeg"):
        logger.error(
            "[publishing] ffmpeg not installed — cannot mix music_asset into video. "
            "Install ffmpeg in social-api / worker images."
        )
        return video_path

    out_path = tempfile.NamedTemporaryFile(suffix="_mixed.mp4", delete=False).name
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        out_path,
    ]
    proc = await _asyncio.create_subprocess_exec(
        *cmd,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:500]
        logger.warning(f"[publishing] ffmpeg mix failed: {err}")
        return video_path  # fall back to original
    logger.info("[publishing] Mixed music into video → %s", out_path)
    return out_path


def _build_post_text(post: Post, platform: str) -> str:
    """Delegate to content_renderer for per-platform adaptation."""
    from app.services.content_renderer import render_post_text
    return render_post_text(post, platform)


def _images_to_pdf(image_paths: list[str], title: str = "Carousel") -> bytes:
    from PIL import Image
    pdf_bytes = io.BytesIO()
    images = [Image.open(p).convert("RGB") for p in image_paths]
    if not images:
        raise ValueError("No images to convert")
    images[0].save(
        pdf_bytes,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=150,
    )
    return pdf_bytes.getvalue()


def _media_public_url(storage_path: str, *, force_jpeg: bool = False) -> str | None:
    """Build a publicly reachable URL for a local upload path.

    Priority:
    1. MEDIA_PUBLIC_BASE_URL + /api/v1/media/view?path=...
    2. /run/tunnel/url (Cloudflare tunnel) + /api/v1/media/view?path=...
    3. R2_PUBLIC_URL + storage_path (for R2-backed assets with relative keys)

    When ``force_jpeg`` is True (Instagram Graph ``image_url``), append
    ``format=jpeg`` so ``/media/view`` re-encodes WebP/HEIC/AVIF to JPEG and
    avoids Meta error 36001 / subcode 2207083 ("image format not supported").
    """
    base = (_settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        try:
            with open("/run/tunnel/url") as _f:
                base = _f.read().strip().rstrip("/")
        except OSError:
            pass
    if base:
        url = f"{base}/api/v1/media/view?path={urllib.parse.quote(storage_path)}"
        if force_jpeg:
            url += "&format=jpeg"
        return url
    # Fall back to R2 public URL for assets stored as relative R2 keys
    r2_base = (_settings.R2_PUBLIC_URL or "").rstrip("/")
    if r2_base and storage_path and not storage_path.startswith("/"):
        # Direct R2 object URLs cannot be re-encoded; callers that need JPEG
        # should store JPEG/PNG originals or use MEDIA_PUBLIC_BASE_URL.
        return f"{r2_base}/{storage_path}"
    return None


# ── Twitter ───────────────────────────────────────────────────────────────────
# Posts via X API v2 (OAuth 2.0 user context, POST /2/tweets).
# Media upload via X API v2 (POST /2/media/upload, chunked
# /2/media/upload/initialize|{id}/append|{id}/finalize) with the account's
# OAuth 2.0 token — requires the ``media.write`` scope. Accounts connected
# before media.write was requested fall back to an OAuth 1.0a user-context
# signature (TWITTER_API_KEY / API_SECRET + ACCESS_TOKEN / ACCESS_TOKEN_SECRET,
# which must belong to the same X account) on the same v2 endpoints.

def _oauth1_auth_header(
    method: str,
    url: str,
    *,
    api_key: str,
    api_secret: str,
    token: str,
    token_secret: str,
    extra_params: dict | None = None,
) -> str:
    """Build an OAuth 1.0a Authorization header using HMAC-SHA1."""
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **(extra_params or {})}
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(all_params.items())
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(sorted_params, safe=""),
    ])
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()  # type: ignore[attr-defined]
    ).decode()
    oauth_params["oauth_signature"] = signature
    header_parts = ', '.join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"


_X_MEDIA_SCOPE_MISSING = "missing media.write scope"
_X_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm",
}


def _x_oauth1_media_signer():
    """OAuth 1.0a signer for the v2 media endpoints, or None if unconfigured."""
    creds = (
        _settings.TWITTER_API_KEY, _settings.TWITTER_API_SECRET,
        _settings.TWITTER_ACCESS_TOKEN, _settings.TWITTER_ACCESS_TOKEN_SECRET,
    )
    if not all(creds):
        return None
    api_key, api_secret, token, token_secret = creds

    def _sign(method: str, url: str, params: dict[str, str] | None = None) -> str:
        return _oauth1_auth_header(
            method, url, api_key=api_key, api_secret=api_secret,
            token=token, token_secret=token_secret, extra_params=params,
        )

    return _sign


def _x_media_client(access_token: str, account: SocialAccount | None) -> TwitterAPIClient:
    """Client for media upload: OAuth 2.0 when the grant has ``media.write``.

    Grants made before media.write was requested 403 on /2/media/upload;
    use the OAuth 1.0a user-context credentials for those until the account
    is reconnected (v2 media endpoints accept either auth).
    """
    scopes = set(getattr(account, "scopes", None) or [])
    if "media.write" not in scopes:
        signer = _x_oauth1_media_signer()
        if signer is not None:
            return TwitterAPIClient(access_token=access_token, media_signer=signer)
    return TwitterAPIClient(access_token=access_token)


async def _twitter_upload_media(
    path: str,
    *,
    access_token: str,
    account: SocialAccount | None = None,
    alt_text: str | None = None,
) -> str:
    """Upload one image / GIF / video via X API v2. Returns the media id.

    Images use the simple upload; GIF and video use the chunked
    initialize → append → finalize (→ status) flow X requires for them.
    Raises ``TwitterAPIError`` on any failure — callers must never fall
    back to posting the tweet without its media. Alt text is best-effort.
    """
    ext = os.path.splitext(path)[1].lower()
    media_type = _X_MEDIA_TYPES.get(ext)
    if media_type is None:
        raise TwitterAPIError(0, f"unsupported media type {ext!r}", path, message=f"X media type not supported: {ext}")
    scopes = set(getattr(account, "scopes", None) or [])
    if account is not None and "media.write" not in scopes and _x_oauth1_media_signer() is None:
        raise TwitterAPIError(
            403,
            _X_MEDIA_SCOPE_MISSING,
            "https://api.x.com/2/media/upload",
            message=(
                "X media upload needs the media.write OAuth 2.0 scope — reconnect the X "
                "account (or configure TWITTER_API_KEY/SECRET + ACCESS_TOKEN/SECRET)"
            ),
        )
    with open(path, "rb") as fh:
        data = fh.read()

    client = _x_media_client(access_token, account)
    if media_type.startswith("video/"):
        media_id = await client.upload_media_chunked(data, media_type, "tweet_video")
    elif media_type == "image/gif":
        media_id = await client.upload_media_chunked(data, media_type, "tweet_gif")
    else:
        media_id = await client.upload_media(
            data, media_category="tweet_image", mime_type=media_type, filename=os.path.basename(path),
        )
    logger.info("[twitter] uploaded media %s (%s)", media_id, media_type)
    if alt_text and not media_type.startswith("video/"):
        try:
            await client.set_media_alt_text(media_id, alt_text)
        except (TwitterAPIError, ValueError) as exc:
            logger.warning("[twitter] alt text not set for media %s: %s", media_id, str(exc)[:200])
    return media_id


async def _x_media_alt_texts(post: Post, db: AsyncSession | None) -> list[str | None]:
    """Alt texts for the post's media assets, in ``post.media_ids`` order."""
    ids = list(dict.fromkeys(str(m) for m in (getattr(post, "media_ids", None) or [])))
    if db is None or not ids:
        return []
    try:
        result = await db.execute(select(MediaAsset).where(MediaAsset.id.in_(post.media_ids)))
        by_id = {str(a.id): (a.alt_text or None) for a in result.scalars().all()}
    except Exception as exc:  # noqa: BLE001 — alt text is optional
        logger.debug("[twitter] alt text lookup failed: %s", exc)
        return []
    return [by_id.get(i) for i in ids]


def _x_weighted_len(text: str) -> int:
    """Approximate X's weighted character count.

    Per the twitter-text spec: characters in ranges U+0000–U+10FF,
    U+2000–U+200D, U+2010–U+201F and U+2032–U+2037 weigh 1; everything
    else (emoji, ellipsis U+2026, CJK, …) weighs 2. Every http(s) URL is
    t.co-wrapped and counts as 23 regardless of length.
    """
    import re as _re

    def _w(ch: str) -> int:
        o = ord(ch)
        light = o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037
        return 1 if light else 2

    urls = _re.findall(r"https?://\S+", text)
    body = _re.sub(r"https?://\S+", "", text)
    return sum(_w(c) for c in body) + 23 * len(urls)


def _fit_x_limit(text: str, limit: int = 280) -> str:
    """Trim ``text`` to X's weighted character limit (word-boundary)."""
    if _x_weighted_len(text) <= limit:
        return text
    words = text.split()
    out: list[str] = []
    cur = ""
    for word in words:
        test = f"{cur} {word}".strip()
        if _x_weighted_len(test) > limit:
            break
        cur = test
        out.append(word)
    trimmed = cur or text[: limit // 2]
    return trimmed.rstrip(" ,;:-") or trimmed


def _split_thread(text: str, limit: int = 275) -> list[str]:
    """Split long text into tweet-sized chunks that form a thread."""
    if len(text) <= limit:
        return [text]
    words = text.split()
    tweets: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > limit:
            if current:
                tweets.append(current)
            current = word
        else:
            current = test
    if current:
        tweets.append(current)
    return tweets


async def _refresh_oauth2_token(account: SocialAccount, db: AsyncSession | None) -> str | None:
    """Refresh an expired OAuth2 access token using the account's refresh token.

    Used at publish time when the platform returns 401 — the scheduled hourly
    refresh may lag (X tokens live 2h) or a past transient failure may have
    marked the account expired. Persisting the rotated refresh token is
    critical on X: refresh tokens are single-use.
    """
    if db is None or not getattr(account, "refresh_token_enc", None):
        return None
    try:
        from app.api.auth import twitter_client
        from app.core.security import encrypt_token as _enc

        refresh_token = decrypt_token(account.refresh_token_enc)
        token = await twitter_client.refresh_token(refresh_token)
        new_access = token.get("access_token")
        if not new_access:
            return None
        account.access_token_enc = _enc(new_access)
        if token.get("refresh_token"):
            account.refresh_token_enc = _enc(token["refresh_token"])
        expires_in = token.get("expires_in")
        if expires_in:
            account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        granted = token.get("scope")
        if isinstance(granted, str) and granted.strip():
            account.scopes = sorted(set(granted.split()))
        account.status = "active"
        await db.commit()
        logger.info("[twitter] refreshed OAuth2 token at publish time")
        return new_access
    except Exception as exc:
        logger.warning("[twitter] publish-time token refresh failed: %s", exc)
        return None


async def _publish_twitter(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
    db: AsyncSession | None = None,
) -> PublishResult:
    """Publish to X/Twitter: official API → X web (tweety) → browser bridge.

    Text + thread splitting via v2 POST /2/tweets. Media upload via v2
    /2/media/upload (images simple; GIF/video chunked) with the OAuth 2.0
    token (media.write) or OAuth 1.0a fallback, plus alt text from the
    media asset. Media is validated up front (≤4 images, or 1 GIF, or 1
    video) and a post is never published without its media: an upload
    failure fails the post (or hands it to the free fallbacks).
    On 401 the OAuth2 token is refreshed once and the request retried —
    X access tokens live only 2h, so self-heal instead of failing the post.
    On 402 credits-depleted / usage-cap the X web fallback takes over when
    ``X_WEB_FALLBACK_ENABLED`` (see ``app.services.x_web``).
    """
    from app.services import x_web

    # Every attached asset must have resolved to a local file — the resolver
    # silently drops assets it could not fetch (R2/MinIO outage, deleted row).
    expected_media = len(dict.fromkeys(str(m) for m in (getattr(post, "media_ids", None) or [])))
    if expected_media and len(media_paths) < expected_media:
        return PublishResult(
            success=False,
            error=(
                f"X post media incomplete: only {len(media_paths)}/{expected_media} media assets could be "
                "resolved — not publishing without all media"
            ),
        )

    try:
        plan = x_web.plan_media(media_paths)
    except x_web.XWebMediaError as exc:
        return PublishResult(success=False, permanent=True, error=f"X post media invalid — not publishing: {exc}")

    tweets = _split_thread(text)

    client = TwitterAPIClient(access_token=access_token)
    media_ids: list[str] = []
    refreshed = False
    if plan.has_media:
        import tempfile as _tempfile

        alt_texts = await _x_media_alt_texts(post, db)
        if len(alt_texts) != len(plan.paths):
            alt_texts = [None] * len(plan.paths)
        workdir = _tempfile.mkdtemp(prefix="x_media_")
        try:
            try:
                upload_paths = x_web.prepare_media(plan, workdir)  # JPEG-normalized
            except x_web.XWebMediaError as exc:
                return PublishResult(success=False, permanent=True, error=f"X post media invalid — not publishing: {exc}")
            for idx, path in enumerate(upload_paths):
                try:
                    try:
                        media_ids.append(await _twitter_upload_media(
                            path, access_token=access_token, account=account, alt_text=alt_texts[idx],
                        ))
                    except TwitterAPIError as auth_exc:
                        # Expired 2h access token: refresh once, then retry.
                        if auth_exc.status_code != 401 or refreshed:
                            raise
                        refreshed = True
                        new_token = await _refresh_oauth2_token(account, db)
                        if not new_token:
                            raise
                        access_token = new_token
                        client = TwitterAPIClient(access_token=access_token)
                        media_ids.append(await _twitter_upload_media(
                            path, access_token=access_token, account=account, alt_text=alt_texts[idx],
                        ))
                except TwitterAPIError as exc:
                    if _is_x_quota_error(exc):
                        return await _publish_twitter_fallbacks(
                            account, text, tweets, post, plan, reason=f"{exc} {exc.response_text}",
                            retry_at=_x_retry_at(exc),
                        )
                    if exc.response_text == _X_MEDIA_SCOPE_MISSING:
                        # No media-capable official auth at all (grant predates
                        # media.write, no OAuth 1.0a creds) — same as the API
                        # being unavailable for this post: free fallbacks/defer.
                        return await _publish_twitter_fallbacks(
                            account, text, tweets, post, plan, reason=str(exc),
                        )
                    if x_web.is_configured():
                        return await _publish_twitter_fallbacks(
                            account, text, tweets, post, plan, reason=f"official media upload failed: {exc}",
                        )
                    return PublishResult(
                        success=False,
                        error=f"X media upload failed — not posting without media: {str(exc)[:300]}",
                    )
        finally:
            import shutil as _shutil

            _shutil.rmtree(workdir, ignore_errors=True)
        if len(media_ids) != len(plan.paths):
            return PublishResult(success=False, error="X media upload incomplete — not posting without media")

    first_id: str | None = None
    last_id: str | None = None

    for i, chunk in enumerate(tweets):
        try:
            # Only attach media to the first tweet in the thread
            tweet_media_ids = media_ids if i == 0 else None
            result = await client.create_tweet(text=chunk, reply_tweet_id=last_id, media_ids=tweet_media_ids)
        except TwitterAPIError as exc:
            if _is_x_quota_error(exc):
                if first_id:
                    # Head tweet already live — never re-post the whole thread.
                    return PublishResult(
                        success=True,
                        platform_post_id=first_id,
                        platform_url=f"https://twitter.com/{account.username}/status/{first_id}",
                        platform_meta={"x_thread": {"incomplete": True, "posted": i, "total": len(tweets), "error": str(exc)[:200]}},
                    )
                # X API write credits / monthly cap — free fallbacks.
                return await _publish_twitter_fallbacks(
                    account, text, tweets, post, plan, reason=f"{exc} {getattr(exc, 'response_text', '')}",
                    retry_at=_x_retry_at(exc),
                )
            if exc.status_code in (401, 403) and not refreshed:
                refreshed = True
                new_token = await _refresh_oauth2_token(account, db)
                if new_token:
                    client = TwitterAPIClient(access_token=new_token)
                    try:
                        result = await client.create_tweet(
                            text=chunk, reply_tweet_id=last_id,
                            media_ids=media_ids if i == 0 else None,
                        )
                    except TwitterAPIError:
                        raise exc
                else:
                    raise
            else:
                raise
        tid = (result.get("data") or {}).get("id", "")
        if first_id is None:
            first_id = tid
        last_id = tid

    return PublishResult(
        success=True,
        platform_post_id=first_id,
        platform_url=f"https://twitter.com/{account.username}/status/{first_id}" if first_id else None,
    )


async def _publish_twitter_fallbacks(
    account: SocialAccount,
    text: str,
    tweets: list[str],
    post: Post,
    plan: Any,
    *,
    reason: str,
    retry_at: datetime | None = None,
) -> PublishResult:
    """Free X fallbacks after the official API refused: x_web → browser bridge.

    ``plan`` is the validated ``x_web.MediaPlan``. The browser bridge can
    only attach images/GIFs, so video posts never reach it (they fail or
    defer instead of going out without the video).

    Capacity outcomes (x_web daily cap / pacing gap / breaker already open /
    rate-limit trip, no fallback available) return ``retry_after`` so the
    worker defers the target instead of failing or skipping it.
    ``retry_at`` is when the official API is expected back (reset headers).
    """
    from app.services import x_web

    web_error: str | None = None
    if x_web.is_configured():
        out = await x_web.publish_via_x_web(
            expected_username=account.username,
            chunks=tweets,
            media_paths=plan.paths,
        )
        if out.status == "ok" and out.first_id:
            handle = (account.username or "").lstrip("@")
            meta: dict[str, Any] = {"provider": "x_web_tweety", "tweet_ids": out.tweet_ids}
            if out.partial:
                meta.update({"thread_incomplete": True, "error": (out.error or "")[:300]})
            return PublishResult(
                success=True,
                platform_post_id=out.first_id,
                platform_url=f"https://x.com/{handle}/status/{out.first_id}" if handle else None,
                platform_meta={"x_web": meta},
            )
        if out.status in ("deferred", "breaker"):
            # Cap slot / pacing gap / breaker reopen time from the guard.
            when = out.retry_after or retry_at or _x_capacity_backoff()
            if retry_at is not None:
                # Whichever path frees up first: official API reset or slot.
                when = min(when, retry_at)
            return PublishResult(success=False, error=out.error, retry_after=when)
        if out.status in ("tripped", "media_error", "identity", "too_long", "ambiguous"):
            return PublishResult(success=False, permanent=True, error=out.error)
        web_error = out.error  # transient — try the browser bridge next

    if plan.kind == "video":
        detail = (
            "X video posts need the X web fallback (the browser bridge cannot attach video)"
            + (f"; X web: {web_error}" if web_error else "; set X_WEB_FALLBACK_ENABLED + X_WEB_AUTH_TOKEN/X_WEB_CT0")
        )
        # x_web transient error or unavailable: the video cannot go out any
        # other way right now — defer (bounded by the worker) instead of
        # burning attempts.
        return _x_capacity_defer_result(f"{detail}. Official: {reason[:200]}", retry_at)

    browser_result = await _publish_twitter_via_browser(account, text, post, plan.paths)
    if browser_result.success:
        return browser_result
    if not browser_result.skipped:
        # Hard failure (e.g. wrong-account session) — surface it
        # through retries/alerts instead of a silent deferral.
        if web_error:
            return dataclasses.replace(
                browser_result, error=f"{web_error}; browser bridge: {browser_result.error}",
            )
        return browser_result
    # Browser bridge soft-unavailable (+ x_web transient error, if any):
    # capacity is missing, nothing is wrong with the post — defer.
    detail = f"{web_error}; browser bridge: {browser_result.error}" if web_error else (browser_result.error or reason)
    return _x_capacity_defer_result(detail, retry_at)


async def _publish_twitter_via_browser(
    account: SocialAccount,
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Post a tweet through the browser bridge (x.com web composer).

    Free fallback used when the X API returns 402 credits-depleted. Media
    paths under /app/uploads are shared with the browser-novnc container
    (same host dir mounted read-only), so the bridge can attach them via
    Playwright's native set_input_files.
    """
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError
    from app.services.browser_orchestrator import browser_session

    image_paths = [
        p for p in media_paths[:4]
        if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
    ]
    if len(image_paths) != len(media_paths):
        # Never publish with a partial/missing media set.
        return PublishResult(
            success=False,
            error=(
                "X browser bridge can only attach up to 4 PNG/JPEG/GIF/WEBP images — "
                "this post's media cannot be attached, not publishing without it"
            ),
        )

    settings = get_settings()
    client = BrowserBridgeClient(settings.BROWSER_BRIDGE_URL, platform="twitter")
    try:
        # Hold the shared-browser lock so messenger pollers can't hijack
        # the session mid-compose. Wait longer than the 90s lock expiry —
        # proceeding without the lock once raced another task and the page
        # died mid-navigation. X's web composer posts a single tweet —
        # trim to the weighted 280-char limit (URLs count 23 via t.co,
        # emoji/ellipsis count double) or Post stays disabled.
        async with browser_session("twitter", client, max_wait=180):
            # Take ownership of the shared browser for twitter BEFORE
            # probing — otherwise our own interactions extend another
            # platform's busy-hold and block the takeover.
            status = await client.session_status()
            if status.get("platform") != "twitter":
                try:
                    # Pollers churn the shared browser continuously; retry
                    # briefly then force-preempt. Must stay well under the
                    # task's 600s soft limit — a single attempt can't eat
                    # the whole batch's budget.
                    await client.start_session("twitter", contention_retries=8)
                except BrowserBridgeError as exc:
                    return _x_quota_skip_result(f"browser busy: {exc.detail}")
            state = await client.is_twitter_logged_in()
            if not state.get("logged_in"):
                # Self-heal: drive the two-step login with stored creds.
                # One attempt only; repeated failures risk lockout.
                login_user = (
                    settings.TWITTER_LOGIN_USERNAME
                    or settings.TWITTER_LOGIN_EMAIL
                    or account.username
                    or ""
                ).lstrip("@")
                if login_user and settings.TWITTER_LOGIN_PASSWORD:
                    login_res = await client.twitter_login(
                        login_user, settings.TWITTER_LOGIN_PASSWORD
                    )
                    if login_res.get("status") != "logged_in":
                        return _x_quota_skip_result(
                            f"browser login failed: {login_res.get('error')}"
                        )
                    # Re-probe so the identity check below sees the fresh session.
                    state = await client.is_twitter_logged_in()

            # Identity check: posting under the wrong X account is brand damage.
            # Fail (alert) rather than silently publishing as a foreign handle.
            expected = (account.username or "").lstrip("@").lower()
            actual = (state.get("handle") or "").lstrip("@").lower()
            if expected and actual and actual != expected:
                return PublishResult(
                    success=False,
                    error=(
                        f"X browser session is logged in as @{actual}, "
                        f"expected @{expected} — re-login the bridge session "
                        "as the correct account before retrying"
                    ),
                )
            if expected and not actual and state.get("logged_in"):
                # Fail-closed: an undetectable handle means we cannot rule
                # out a foreign session — better an alerted failure than a
                # post under the wrong account.
                return PublishResult(
                    success=False,
                    error=(
                        "X browser session handle undetectable — cannot "
                        f"verify the logged-in account is @{expected}; "
                        "re-login the bridge session before retrying"
                    ),
                )
            res = await client.post_tweet(_fit_x_limit(text), image_paths or None)
    except Exception as exc:
        return _x_quota_skip_result(f"browser fallback failed: {exc}")
    if res.get("status") != "ok":
        return _x_quota_skip_result(
            f"browser fallback failed: {res.get('error') or res.get('message')}"
        )
    url = res.get("url") or (f"https://x.com/{account.username}" if account.username else None)
    post_id = url.rstrip("/").rsplit("/status/", 1)[-1] if url and "/status/" in url else None
    return PublishResult(success=True, platform_post_id=post_id, platform_url=url)


# ── LinkedIn ──────────────────────────────────────────────────────────────────

def _linkedin_author_urn(account: SocialAccount, client: LinkedInAPIClient) -> str:
    meta = account.meta_data or {}
    if meta.get("author_urn"):
        return str(meta["author_urn"])
    account_type = (meta.get("account_type") or "person").lower()
    return client._author_urn(account.account_id, account_type)


def _has_linkedin_browser_session(account: SocialAccount) -> bool:
    """Check if the account has a browser storage state for the sidecar."""
    meta = account.meta_data or {}
    return bool(meta.get("browser_storage_state"))


async def _publish_linkedin_via_sidecar(
    account: SocialAccount,
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Publish to a personal LinkedIn profile or Company Page via the browser sidecar.

    Supports text, image (single or multi), and link posts.
    Falls back to the official LinkedIn API if the sidecar fails.
    """
    meta = account.meta_data or {}
    storage = meta.get("browser_storage_state")
    if not storage:
        return PublishResult(
            success=False,
            error="LinkedIn browser session not found. Call POST /login first.",
        )

    client = LinkedInSidecarClient()
    try:
        await client.set_session(storage)
    except LinkedInSidecarError as e:
        return PublishResult(success=False, error=e.detail)

    try:
        image_paths = [p for p in media_paths if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]

        # Company page: use company post endpoints
        if account.account_type == "organization":
            vanity = (meta.get("vanity_name") or account.username or "").replace("_", "-")
            if not vanity:
                return PublishResult(success=False, error="Company vanity name not found in account metadata")

            if image_paths:
                images = []
                for p in image_paths[:9]:
                    with open(p, "rb") as fh:
                        img_b64 = base64.b64encode(fh.read()).decode()
                    images.append({"image_base64": img_b64, "filename": os.path.basename(p)})
                result = await client.company_post_image(vanity=vanity, images=images, message=text)
            else:
                result = await client.company_post_text(vanity=vanity, message=text)
        else:
            # Personal profile
            if image_paths:
                images = []
                for p in image_paths[:9]:
                    with open(p, "rb") as fh:
                        img_b64 = base64.b64encode(fh.read()).decode()
                    images.append({"image_base64": img_b64, "filename": os.path.basename(p)})
                result = await client.post_image(images=images, message=text)
            elif post.link_url:
                result = await client.post_link(url=post.link_url, message=text)
            else:
                result = await client.post_text(message=text)

        post_url = result.get("url")
        post_id = result.get("post_id") or (post_url.split("/")[-1] if post_url else None)
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=post_url,
        )
    except LinkedInSidecarError as e:
        return PublishResult(success=False, error=e.detail)


async def _publish_linkedin(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    # If we have a browser session, try the sidecar first (supports personal + company)
    if _has_linkedin_browser_session(account):
        result = await _publish_linkedin_via_sidecar(account, text, post, media_paths)
        if result.success:
            return result
        # If sidecar fails, fall through to official API below

    client = LinkedInAPIClient(access_token=access_token)
    author_urn = _linkedin_author_urn(account, client)
    post_title = (post.content_text or "Carousel")[:80]

    # Detect media types — video and PDF get dedicated upload flows;
    # everything else is treated as an image.
    video_paths = [
        p for p in media_paths
        if p.lower().endswith((".mp4", ".mov", ".webm", ".avi", ".mkv"))
    ]
    pdf_paths = [p for p in media_paths if p.lower().endswith(".pdf")]
    image_paths = [
        p for p in media_paths
        if p not in video_paths and not p.lower().endswith(".pdf")
    ]

    if video_paths:
        # Native video takes precedence — a video post cannot also carry
        # images/documents on LinkedIn.
        with open(video_paths[0], "rb") as fh:
            video_bytes = fh.read()
        result = await client.create_video_post(
            author_urn=author_urn,
            commentary=text,
            video_bytes=video_bytes,
            title=post_title,
        )
    elif pdf_paths:
        # Upload the first PDF as a document post (LinkedIn carousel).
        # If there are multiple PDFs, combine them; if there are also
        # images, they are ignored (PDF takes precedence as carousel).
        if len(pdf_paths) == 1:
            with open(pdf_paths[0], "rb") as fh:
                pdf_bytes = fh.read()
        else:
            # Multiple PDFs — merge them (rare case)
            pdf_bytes = io.BytesIO()
            for p in pdf_paths:
                with open(p, "rb") as fh:
                    pdf_bytes.write(fh.read())
            pdf_bytes = pdf_bytes.getvalue()
        result = await client.create_document_post(
            author_urn=author_urn,
            commentary=text,
            pdf_bytes=pdf_bytes,
            title=post_title,
        )
    elif len(image_paths) >= 2:
        pdf_bytes = _images_to_pdf(image_paths, title=post_title)
        result = await client.create_document_post(
            author_urn=author_urn,
            commentary=text,
            pdf_bytes=pdf_bytes,
            title=post_title,
        )
    elif len(image_paths) == 1:
        result = await client.create_multi_image_post(
            author_urn=author_urn,
            commentary=text,
            media_paths=image_paths,
        )
    else:
        override = post.link_preview_override or {}
        result = await client.create_post(
            author_urn=author_urn,
            commentary=text,
            link_url=post.link_url,
            link_title=override.get("title", ""),
            link_description=override.get("description", ""),
        )

    return PublishResult(**dataclasses.asdict(result))


# ── Facebook ──────────────────────────────────────────────────────────────────
# The stored account is the Facebook user.  To post as a Page we must
# exchange the user token for a Page access token at publish time.

async def _facebook_page_token(user_token: str, page_id: str) -> str:
    """Return a Page access token for `page_id`, or fall back to `user_token`."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            facebook_graph_url("me/accounts"),
            params={"access_token": user_token, "fields": "id,access_token"},
        )
    if resp.status_code != 200:
        return user_token
    for page in resp.json().get("data", []):
        if page.get("id") == page_id:
            return page.get("access_token") or user_token
    # page_id not in managed pages — return user token as-is
    return user_token


def _has_facebook_browser_session(account: SocialAccount) -> bool:
    """Check if the account has a browser storage state for the sidecar."""
    meta = account.meta_data or {}
    return bool(meta.get("browser_storage_state"))


async def _publish_facebook_via_sidecar(
    account: SocialAccount,
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Publish to a personal Facebook profile via the browser sidecar.

    Supports text, photo (single or multi), link, and video posts.
    Falls back to PublishResult with an error if the sidecar fails.
    """
    meta = account.meta_data or {}
    storage = meta.get("browser_storage_state")
    if not storage:
        return PublishResult(
            success=False,
            error="Facebook browser session not found. Call POST /login first.",
        )

    client = FacebookSidecarClient()
    try:
        await client.set_session(storage)
    except FacebookSidecarError as e:
        return PublishResult(success=False, error=e.detail)

    # Profile posts default to public — the growth/Stars campaign relies on
    # unconnected distribution, and Friends-only posts can't earn followers.
    # Override per-post via platform_specific.facebook_privacy.
    privacy = str(
        (getattr(post, "platform_specific", None) or {}).get(
            "facebook_privacy", "public"
        )
    )

    try:
        # Determine post type
        image_paths = [p for p in media_paths if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]
        video_paths = [p for p in media_paths if p.lower().endswith((".mp4", ".mov", ".webm", ".avi"))]

        if video_paths:
            # Video post
            with open(video_paths[0], "rb") as fh:
                video_bytes = fh.read()
            result = await client.post_video(video_bytes, message=text, privacy=privacy)
        elif image_paths:
            # Photo post (single or multi)
            images = []
            for p in image_paths[:10]:
                with open(p, "rb") as fh:
                    img_b64 = base64.b64encode(fh.read()).decode()
                images.append({"image_base64": img_b64, "filename": os.path.basename(p)})
            result = await client.post_photo(images=images, message=text, privacy=privacy)
        elif post.link_url:
            # Link post
            result = await client.post_link(url=post.link_url, message=text, privacy=privacy)
        else:
            # Text-only post
            result = await client.post_text(message=text, privacy=privacy)

        post_url = result.get("url")
        post_id = result.get("post_id") or (post_url.split("/")[-1] if post_url else None)
        if not post_id and not post_url:
            return PublishResult(
                success=False,
                error="Facebook sidecar returned no post id or URL — cannot confirm publish",
            )
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=post_url,
        )
    except FacebookSidecarError as e:
        return PublishResult(success=False, error=e.detail)


async def _publish_facebook(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    # Groups API removed in Graph v19+ — soft-skip instead of retrying OAuthException.
    acct_type = (account.account_type or "").lower()
    meta_type = str((getattr(account, "meta_data", None) or {}).get("account_type") or "").lower()
    if acct_type == "group" or meta_type == "group":
        return _facebook_group_skip_result(
            f"account_type={account.account_type!r} account_id={account.account_id}"
        )

    # Personal profile (type=user) with a browser session → use sidecar.
    # Do NOT fall through to Graph on sidecar failure — Meta removed user-
    # profile posting from the Graph API entirely, so the fallback can only
    # produce the misleading "#200 group" error, and it masks the real
    # sidecar failure as a terminal "skipped" instead of a retryable fail.
    if account.account_type == "user" and _has_facebook_browser_session(account):
        result = await _publish_facebook_via_sidecar(account, text, post, media_paths)
        return result

    page_id = account.account_id
    # access_token is stored as the page token from OAuth callback.
    # Fall back to dynamic lookup for accounts connected before this fix.
    try:
        page_token = await _facebook_page_token(access_token, page_id)
    except Exception as exc:
        if _err_has(str(exc), _FB_GROUP_MARKERS):
            return _facebook_group_skip_result(str(exc))
        raise

    # Text/link posts go through the real FacebookAPIClient.
    # Photo albums still use Facebook's unpublished upload flow (file bytes).
    fb_client = FacebookAPIClient(access_token=page_token, page_id=page_id)

    try:
        if not media_paths:
            result = await fb_client.create_post(message=text, link=post.link_url)
            fb_post_id = result.get("id", "")
            if not fb_post_id:
                return PublishResult(
                    success=False,
                    error="Facebook API returned no post id — cannot confirm publish",
                )
            return PublishResult(
                success=True,
                platform_post_id=fb_post_id,
                platform_url=f"https://www.facebook.com/{fb_post_id}",
            )

        graph_base = f"{FACEBOOK_GRAPH_BASE}/{FACEBOOK_GRAPH_VERSION}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            # Upload each photo as unpublished, then publish as album/multi-photo
            photo_ids: list[str] = []
            for path in media_paths[:10]:
                with open(path, "rb") as fh:
                    img_bytes = fh.read()
                r = await client.post(
                    f"{graph_base}/{page_id}/photos",
                    data={"access_token": page_token, "published": "false"},
                    files={"source": ("image.png", img_bytes, "image/png")},
                )
                if r.status_code == 200:
                    pid = r.json().get("id")
                    if pid:
                        photo_ids.append(pid)
                elif _err_has(r.text, _FB_GROUP_MARKERS):
                    return _facebook_group_skip_result(r.text)

            if photo_ids:
                # Multi-photo post via /feed with attached_media
                attached = [{"media_fbid": pid} for pid in photo_ids]
                import json as _json
                r = await client.post(
                    f"{graph_base}/{page_id}/feed",
                    data={
                        "message": text,
                        "access_token": page_token,
                        "attached_media": _json.dumps(attached),
                    },
                )
                if r.status_code >= 400 and _err_has(r.text, _FB_GROUP_MARKERS):
                    return _facebook_group_skip_result(r.text)
                r.raise_for_status()
                fb_post_id = r.json().get("id", "")
            else:
                # Photo uploads all failed — fall through to text post
                result = await fb_client.create_post(message=text, link=post.link_url)
                fb_post_id = result.get("id", "")

        if not fb_post_id:
            return PublishResult(
                success=False,
                error="Facebook API returned no post id — cannot confirm publish",
            )
        return PublishResult(
            success=True,
            platform_post_id=fb_post_id,
            platform_url=f"https://www.facebook.com/{fb_post_id}",
        )
    except Exception as exc:
        if _err_has(str(exc), _FB_GROUP_MARKERS):
            return _facebook_group_skip_result(str(exc))
        raise


# ── Instagram ─────────────────────────────────────────────────────────────────
# Two publishing paths:
#
# 1. Private API sidecar (primary) — uses the aiograpi-rest Docker sidecar
#    with a logged-in session. Supports photo, video, carousel, and story
#    uploads from local files (no public URL needed). Works with any account
#    type (personal, creator, business). Requires a valid session_id stored
#    in social_accounts.meta_data["private_api_session_id"].
#
# 2. Graph API (fallback) — requires a Business/Creator account linked to a
#    Facebook Page and publicly reachable image URLs (MEDIA_PUBLIC_BASE_URL
#    or Cloudflare Tunnel). Used when no sidecar session is available.

async def _instagram_public_urls(
    storage_paths: list[str],
    post: Post,
    db: AsyncSession | None = None,
) -> list[str]:
    """Resolve public URLs for media assets.

    Priority:
    1. platform_specific.instagram.image_urls (manual override)
    2. R2_PUBLIC_URL + storage_path for R2 assets already JPEG/PNG —
       pub-*.r2.dev has no Access/bot layer between Meta's media fetcher
       and the bytes, so it survives the challenges that intermittently
       fail /media/view fetches (Meta 9004 "media download failed")
    3. MEDIA_PUBLIC_BASE_URL + /api/v1/media/view?path=...
    4. Empty list (image posting not available)
    """
    override: list[str] = (post.platform_specific or {}).get("instagram", {}).get("image_urls", [])
    single: str | None = (post.platform_specific or {}).get("instagram", {}).get("image_url")
    if single and not override:
        override = [single]
    if override:
        return override

    meta_by_path: dict[str, MediaAsset] = {}
    if db is not None and post.media_ids:
        assets = (
            await db.execute(select(MediaAsset).where(MediaAsset.id.in_(post.media_ids)))
        ).scalars().all()
        meta_by_path = {a.storage_path: a for a in assets if a.storage_path}
    r2_base = (_settings.R2_PUBLIC_URL or "").rstrip("/")

    urls: list[str] = []
    for sp in storage_paths:
        asset = meta_by_path.get(sp)
        if (
            asset is not None
            and r2_base
            and (asset.storage_backend or "").lower() == "r2"
            and (asset.mime_type or "").lower() in ("image/jpeg", "image/png")
            and not sp.startswith("/")
        ):
            urls.append(f"{r2_base}/{sp}")
            continue
        # Always request JPEG from /media/view so Graph never sees WebP/HEIC/AVIF.
        url = _media_public_url(sp, force_jpeg=True)
        if url:
            urls.append(url)
    return urls


def _sidecar_file_path(host_path: str) -> str | None:
    """Map a host-side upload path to the sidecar container's view.

    The uploads directory is mounted read-only at /uploads inside the
    instagram-private-api container.  Host paths like
    ``/app/uploads/2024/01/img.jpg`` map to ``/uploads/2024/01/img.jpg``.
    """
    if not host_path:
        return None
    clean = host_path.lstrip("/")
    # Strip leading "app/" if present (social-api stores uploads under /app/uploads)
    if clean.startswith("app/uploads/"):
        clean = clean[4:]  # → "uploads/..."
    # Now clean should start with "uploads/"
    if clean.startswith("uploads/"):
        return f"/{clean}"
    # Already an absolute path starting with /uploads
    if host_path.startswith("/uploads/"):
        return host_path
    # Relative path without uploads/ prefix — prepend it
    if not host_path.startswith("/"):
        return f"/uploads/{clean}"
    return None


async def _publish_instagram_via_web(
    account: SocialAccount,
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Publish to Instagram via the **web API** (rupload_igphoto).

    This is the **primary** Instagram publishing path. It uses the browser
    ``sessionid`` cookie directly against ``www.instagram.com`` endpoints —
    the same flow the Instagram web app uses. It bypasses the private mobile
    API (``i.instagram.com``) which often rejects browser sessions with
    ``login_required``.

    Requires ``private_api_session_id``, ``private_api_csrf_token``,
    and ``private_api_ds_user_id`` in the account's meta_data.
    Uses local file paths (worker reads files from the shared uploads volume).

    Supports:
        - Single photo posts
        - Carousel (multi-photo) posts up to 10 images
    Videos are not yet supported via this path.
    """
    if not media_paths:
        return PublishResult(
            success=False,
            skipped=True,
            error="Instagram requires at least one image or video. Set media on the post.",
        )

    meta = account.meta_data or {}
    session_id = decrypt_field(meta.get("private_api_session_id"))
    csrf_token = meta.get("private_api_csrf_token")
    ds_user_id = meta.get("private_api_ds_user_id")
    if not session_id or not csrf_token or not ds_user_id:
        return PublishResult(
            success=False,
            error="Instagram web API session incomplete (need sessionid, csrftoken, ds_user_id).",
        )

    # Filter to image files only (videos not yet supported via web API)
    image_paths = []
    for fp in media_paths[:10]:
        lower = fp.lower()
        if lower.endswith((".mp4", ".mov", ".webm", ".avi")):
            return PublishResult(
                success=False,
                error="Video upload via Instagram web API is not yet supported. Use sidecar or Graph API.",
            )
        image_paths.append(fp)

    is_carousel = len(image_paths) > 1

    cookie_header = (
        f"sessionid={session_id}; csrftoken={csrf_token}; ds_user_id={ds_user_id}"
    )
    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "X-IG-App-ID": "936619743392459",
        "x-csrftoken": csrf_token,
        "Cookie": cookie_header,
    }
    caption = text[:2200]

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: Upload each photo via rupload_igphoto
        upload_ids: list[str] = []
        upload_dims: list[tuple[int, int]] = []  # (width, height) per image
        for idx, fp in enumerate(image_paths):
            try:
                from PIL import Image
                img = Image.open(fp)
                width, height = img.size
            except Exception as exc:
                return PublishResult(
                    success=False,
                    error=f"Failed to read image {idx + 1} for web API upload: {exc}",
                )

            with open(fp, "rb") as f:
                photo_bytes = f.read()

            upload_id = str(int(time.time() * 1000)) + str(idx)
            entity_name = f"fb_uploader_{upload_id}"

            rupload_params = {
                "media_type": 1,
                "upload_id": upload_id,
                "upload_media_height": height,
                "upload_media_width": width,
            }
            if is_carousel:
                rupload_params["is_sidecar"] = "1"

            rupload_headers = {
                **base_headers,
                "X-Entity-Name": entity_name,
                "X-Entity-Length": str(len(photo_bytes)),
                "X-Entity-Type": "image/jpeg",
                "Offset": "0",
                "X-Instagram-Rupload-Params": json.dumps(rupload_params),
                "Content-Type": "application/octet-stream",
            }

            rupload_url = f"https://www.instagram.com/rupload_igphoto/{entity_name}"

            try:
                resp = await client.post(rupload_url, content=photo_bytes, headers=rupload_headers)
            except httpx.HTTPError as exc:
                return PublishResult(success=False, error=f"Web API upload {idx + 1} failed: {exc}")

            if resp.status_code != 200:
                detail = resp.text[:300]
                return PublishResult(
                    success=False,
                    error=f"Instagram web API rupload {idx + 1} failed (HTTP {resp.status_code}): {detail}",
                )

            upload_resp = resp.json()
            if upload_resp.get("status") != "ok":
                return PublishResult(
                    success=False,
                    error=f"Instagram web API rupload {idx + 1} status not ok: {upload_resp}",
                )

            upload_ids.append(upload_id)
            upload_dims.append((width, height))

        # Step 2: Configure the media (create the post)
        configure_headers = {
            **base_headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.instagram.com/create/details/",
            "Origin": "https://www.instagram.com",
        }

        if is_carousel:
            # Carousel: configure with children_metadata (include width/height per item)
            # Format matches instagram-private-api: extra, edits, device as JSON strings per child
            device_payload = json.dumps({
                "manufacturer": "Xiaomi",
                "model": "MI 5",
                "android_version": 24,
                "android_release": "7.0",
            })
            children_metadata = json.dumps([
                {
                    "upload_id": uid,
                    "width": w,
                    "height": h,
                    "timezone_offset": "0",
                    "caption": None,
                    "source_type": "4",
                    "extra": json.dumps({"source_width": w, "source_height": h}),
                    "edits": json.dumps({
                        "crop_original_size": [w, h],
                        "crop_center": [0.0, -0.0],
                        "crop_zoom": 1.0,
                    }),
                    "device": device_payload,
                }
                for uid, (w, h) in zip(upload_ids, upload_dims)
            ])
            client_sidecar_id = str(int(time.time() * 1000))
            configure_data = {
                "_csrftoken": csrf_token,
                "_uid": ds_user_id,
                "_uuid": "android-e021b636049dc0e9",
                "caption": caption,
                "children_metadata": children_metadata,
                "client_sidecar_id": client_sidecar_id,
                "timezone_offset": "0",
                "source_type": "4",
                "device_id": "android-e021b636049dc0e9",
                "device": device_payload,
            }
            configure_url = "https://www.instagram.com/api/v1/media/configure_sidecar/"
        else:
            # Single photo
            configure_data = {
                "caption": caption,
                "upload_id": upload_ids[0],
                "use_custom_tags": "1",
                "manual_timestamp": "0",
                "source_type": "1",
                "device_id": "android-e021b636049dc0e9",
            }
            configure_url = "https://www.instagram.com/api/v1/media/configure/"

        try:
            resp2 = await client.post(
                configure_url,
                data=configure_data,
                headers=configure_headers,
            )
        except httpx.HTTPError as exc:
            return PublishResult(success=False, error=f"Web API configure request failed: {exc}")

        if resp2.status_code != 200:
            detail = resp2.text[:300]
            return PublishResult(
                success=False,
                error=f"Instagram web API configure failed (HTTP {resp2.status_code}): {detail}",
            )

        try:
            configure_resp = resp2.json()
        except Exception:
            return PublishResult(
                success=False,
                error=f"Instagram web API configure returned non-JSON (HTTP {resp2.status_code}): {resp2.text[:300]}",
            )
        media = configure_resp.get("media", {})
        media_id = str(media.get("id") or "")
        code = media.get("code") or ""
        platform_url = f"https://www.instagram.com/p/{code}/" if code else None

        return PublishResult(
            success=True,
            platform_post_id=media_id,
            platform_url=platform_url,
        )


async def _publish_instagram_via_sidecar(
    account: SocialAccount,
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Publish via the aiograpi-rest private API sidecar.

    Requires ``private_api_session_id`` in the account's meta_data.
    Uses local file paths (uploads volume mounted in the sidecar at /uploads).
    """
    # No media → text-only is not supported by Instagram (check before auth
    # so the soft-skip fires even when the sidecar session is missing).
    if not media_paths:
        return PublishResult(
            success=False,
            skipped=True,
            error="Instagram requires at least one image or video. Set media on the post.",
        )

    meta = account.meta_data or {}
    session_id = decrypt_field(meta.get("private_api_session_id"))
    if not session_id:
        return PublishResult(
            success=False,
            error=(
                "Instagram private API session not found. "
                "Log in via the Accounts page (POST /api/v1/profile/{account_id}/login) "
                "to establish a sidecar session, or reconnect with a Business account "
                "for Graph API fallback."
            ),
        )

    client = InstagramPrivateAPIClient(_settings.INSTAGRAM_PRIVATE_API_URL)
    caption = text[:2200]

    # Re-establish the session in the sidecar with the configured proxy so
    # aiograpi routes Instagram traffic through WARP (fixes DNS failures after
    # container restarts that clear in-memory session state).
    proxy = (_settings.INSTAGRAM_PROXY or "").strip() or None
    try:
        await client.login_by_sessionid(session_id=session_id, proxy=proxy)
    except Exception:
        pass  # best-effort; proceed with upload anyway

    # The upload_photo/upload_video methods now send file bytes via
    # multipart, so we pass the worker-container paths directly (the
    # files are on the shared uploads volume).
    try:
        if len(media_paths) == 1:
            fp = media_paths[0]
            lower = fp.lower()
            if lower.endswith((".mp4", ".mov", ".webm", ".avi")):
                result = await client.upload_video(
                    session_id=session_id,
                    file_path=fp,
                    caption=caption,
                )
            else:
                result = await client.upload_photo(
                    session_id=session_id,
                    file_path=fp,
                    caption=caption,
                )
        else:
            # Carousel: up to 10 items
            result = await client.upload_album(
                session_id=session_id,
                file_paths=media_paths[:10],
                caption=caption,
            )
    except InstagramPrivateAPIError as exc:
        # 500 from sidecar usually means LoginRequired / session expired
        detail_lower = (exc.detail or "").lower()
        if exc.status_code in (401, 500) or "login_required" in detail_lower or "loginrequired" in detail_lower:
            return PublishResult(
                success=False,
                error=(
                    "Instagram private API session expired. "
                    "Re-login via the Accounts page to restore the sidecar session."
                ),
            )
        return PublishResult(
            success=False,
            error=f"Instagram private API publish failed: {exc.detail}",
        )

    media_id = str(result.get("id") or result.get("pk") or "")
    code = result.get("code") or ""
    platform_url = f"https://www.instagram.com/p/{code}/" if code else None
    return PublishResult(
        success=True,
        platform_post_id=media_id,
        platform_url=platform_url,
    )


async def _instagram_find_live(
    client: InstagramAPIClient, caption: str, *, within_minutes: int = 30
) -> dict[str, Any] | None:
    """Return the live media dict if a matching caption was posted recently.

    Meta's ``media_publish`` can return 403 ``error_subcode 2207051``
    ("Application request limit reached") while the post goes live anyway —
    a documented false-negative that produced duplicate posts on every retry.
    Checking the feed before failing/retrying makes the publish idempotent.
    """
    marker = " ".join((caption or "").split())[:60]
    if not marker:
        return None
    cutoff = datetime.now(UTC) - timedelta(minutes=within_minutes)
    try:
        media = await client.list_recent_media()
    except Exception:
        return None
    for m in media:
        if " ".join((m.get("caption") or "").split())[:60] != marker:
            continue
        try:
            when = datetime.fromisoformat((m.get("timestamp") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= cutoff:
            return m
    return None


async def _publish_instagram_via_graph(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None,
    db: AsyncSession | None = None,
) -> PublishResult:
    """Publish via the Instagram Graph API (fallback)."""
    ig_user_id = account.account_id

    # Detect fallback accounts (FB user ID used because no IG Business account found)
    meta = account.meta_data or {}
    if meta.get("account_type", "person") == "person" and not meta.get("ig_business_id"):
        async with httpx.AsyncClient(timeout=10.0) as probe:
            r = await probe.get(
                facebook_graph_url(str(ig_user_id)),
                params={"fields": "account_type", "access_token": access_token},
            )
            data = r.json()
            if data.get("account_type") not in ("BUSINESS", "CREATOR", None):
                return PublishResult(
                    success=False,
                    # Account-type requirement — retrying cannot help; the
                    # account must be switched to Professional in the IG app.
                    skipped=True,
                    error=(
                        "Instagram posting requires a Business or Creator account linked to a Facebook Page. "
                        "In Instagram app: Settings → Account → Switch to Professional Account."
                    ),
                )

    image_urls = await _instagram_public_urls(storage_paths or [], post, db)
    caption = text[:2200]

    if not image_urls:
        if media_paths:
            return PublishResult(
                success=False,
                # Public-URL resolution is a deployment-config gap
                # (MEDIA_PUBLIC_BASE_URL) — retrying cannot help.
                skipped=True,
                error=(
                    "Instagram requires publicly accessible image URLs. "
                    "Set MEDIA_PUBLIC_BASE_URL in .env to a public-facing URL "
                    "(e.g. an ngrok tunnel or Cloudflare Tunnel) and restart the API."
                ),
            )
        return PublishResult(
            success=False,
            # Text-only post on a feed platform — content gap, not transient.
            skipped=True,
            error="Instagram requires at least one image. Set an image on the post.",
        )

    is_business_login = (account.meta_data or {}).get("login_type") == "business_login"
    client = InstagramAPIClient(
        access_token=access_token,
        ig_user_id=ig_user_id,
        use_business_login_api=is_business_login,
    )
    publish_attempted = False
    try:
        if len(image_urls) == 1:
            creation_id = await client.create_image_container(
                image_url=image_urls[0],
                caption=caption,
            )
        else:
            child_ids = [
                await client.create_carousel_item(image_url=url)
                for url in image_urls[:10]
            ]
            creation_id = await client.create_carousel_container(
                children_ids=child_ids,
                caption=caption,
            )
        # IG processes containers asynchronously — publishing before the
        # container reaches FINISHED fails with 9007 "Media ID is not
        # available". Poll until ready (images are usually quick).
        await client.wait_for_container_ready(creation_id, timeout=60.0)
        publish_attempted = True
        media_id = await client.publish_container(creation_id)
    except InstagramAPIError as exc:
        # 2207051 "Application request limit reached" is a documented false
        # negative — the post frequently goes live server-side. Only errors
        # at/after media_publish can mask a live post; earlier failures
        # (container create/wait) cannot produce one.
        if publish_attempted:
            # The media list can take tens of seconds to index a fresh
            # publish (observed lag beyond a single short settle), so poll
            # with backoff; anything longer is left to the delayed reconcile.
            for delay in (4, 10, 20):
                await asyncio.sleep(delay)
                live = await _instagram_find_live(client, caption)
                if live:
                    logger.info(
                        "Instagram media_publish errored but post is live: %s",
                        live.get("id"),
                    )
                    return PublishResult(
                        success=True,
                        platform_post_id=str(live.get("id") or ""),
                        platform_url=live.get("permalink"),
                    )
        return PublishResult(
            success=False,
            error=f"Instagram publish failed: {exc}",
            ambiguous=publish_attempted,
        )
    except TimeoutError as exc:
        # httpx.TimeoutException subclasses TimeoutError — this covers both the
        # container-wait timeout (publish not attempted, not ambiguous) and a
        # publish request timeout (ambiguous: the post may be live).
        if publish_attempted:
            for delay in (4, 10, 20):
                await asyncio.sleep(delay)
                live = await _instagram_find_live(client, caption)
                if live:
                    return PublishResult(
                        success=True,
                        platform_post_id=str(live.get("id") or ""),
                        platform_url=live.get("permalink"),
                    )
        return PublishResult(
            success=False,
            error=f"Instagram publish failed: {exc}",
            ambiguous=publish_attempted,
        )

    return PublishResult(
        success=True,
        platform_post_id=media_id,
        platform_url=f"https://www.instagram.com/p/{media_id}" if media_id else None,
    )


async def _publish_instagram_via_instagrapi(
    text: str,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    """Publish via instagrapi (direct Python private mobile API).

    Uses INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD from settings.  Session is
    cached on the uploads volume; re-login happens automatically when the
    cached session expires.  This path works for any account type (personal,
    creator, business) without Meta App Review.
    """
    if not media_paths:
        return PublishResult(
            success=False,
            skipped=True,
            error="Instagram requires at least one image or video.",
        )

    username = (_settings.INSTAGRAM_USERNAME or "").strip()
    password = (_settings.INSTAGRAM_PASSWORD or "").strip()
    if not username or not password:
        return PublishResult(
            success=False,
            error="INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD are not set.",
        )

    proxy = (_settings.INSTAGRAM_PROXY or "").strip() or None
    client = InstagrapiClient(username=username, password=password, proxy=proxy)
    caption = text[:2200]

    try:
        if len(media_paths) == 1:
            fp = media_paths[0]
            if fp.lower().endswith((".mp4", ".mov", ".webm", ".avi")):
                result = await client.upload_video(fp, caption)
            else:
                result = await client.upload_photo(fp, caption)
        else:
            result = await client.upload_album(media_paths[:10], caption)
    except InstagrapiError as exc:
        return PublishResult(success=False, error=f"Instagram (instagrapi) publish failed: {exc}")

    media_id = str(result.get("id") or result.get("pk") or "")
    code = result.get("code") or ""
    return PublishResult(
        success=True,
        platform_post_id=media_id,
        platform_url=f"https://www.instagram.com/p/{code}/" if code else None,
    )


async def _publish_instagram(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
    db: AsyncSession | None = None,
) -> PublishResult:
    """Publish to Instagram.

    Publishing priority (first success wins):
        1. **Business Login Graph API** (graph.instagram.com) — when the
           account was connected via Instagram Business Login (instagram2).
           Uses the Instagram user token directly; no Facebook Page linkage
           required. This is the official, most reliable path.
        2. **instagrapi** — direct Python private mobile API. Requires
           INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in env. Works for any
           account type without Meta App Review.
        3. **Web API** (rupload_igphoto) — uses browser sessionid cookie
           directly against www.instagram.com. Requires private_api_session_id,
           private_api_csrf_token, private_api_ds_user_id in meta_data.
        4. **Sidecar** (aiograpi-rest private mobile API) — fallback for
           video uploads or when the above paths are unavailable.
        5. **Facebook Login Graph API** (graph.facebook.com) — last resort
           (requires Meta App Review + Page-Instagram linkage).
    """
    # Content rule: Instagram has no text-only feed posts (Graph / private / web).
    if not media_paths:
        return PublishResult(
            success=False,
            skipped=True,
            error=(
                "Instagram requires at least one image or video. "
                "Text-only posts are rejected — attach media or remove the IG target."
            ),
        )

    meta = account.meta_data or {}

    # Duplicate guard: a previous attempt may have published while reporting
    # a failure (the 2207051 false-negative, a crash after media_publish, or
    # a queue retry). Check the feed before spending another attempt — any
    # path's successful publish is visible through the Graph media list.
    try:
        check_client = InstagramAPIClient(
            access_token=access_token,
            ig_user_id=account.account_id,
            use_business_login_api=(meta.get("login_type") == "business_login"),
        )
        live = await _instagram_find_live(check_client, text)
        if live:
            logger.info("Instagram post already live (idempotent publish): %s", live.get("id"))
            return PublishResult(
                success=True,
                platform_post_id=str(live.get("id") or ""),
                platform_url=live.get("permalink"),
            )
    except Exception:
        pass  # best-effort guard — the paths below surface their own errors

    # 1. Business Login Graph API (graph.instagram.com) — highest priority
    # when the account was connected via Instagram Business Login (instagram2).
    # This path uses the Instagram user token directly against graph.instagram.com
    # and does NOT require a Facebook Page to be linked.
    if meta.get("login_type") == "business_login":
        graph_result = await _publish_instagram_via_graph(
            access_token, text, account, post, media_paths, storage_paths, db,
        )
        if graph_result.success:
            return graph_result
        if graph_result.ambiguous:
            # media_publish may have landed despite the error — trying the
            # fallback chain now would publish a duplicate. The queue worker
            # schedules a delayed feed reconciliation for ambiguous failures.
            return graph_result
        logger.warning("Business Login Graph API failed: %s — trying fallback paths", graph_result.error)

    # 2. instagrapi (direct Python) — primary fallback when credentials are set
    if (_settings.INSTAGRAM_USERNAME or "").strip() and (_settings.INSTAGRAM_PASSWORD or "").strip():
        ig_result = await _publish_instagram_via_instagrapi(text, post, media_paths)
        if ig_result.success:
            return ig_result
        logger.warning("instagrapi path failed: %s — trying fallback paths", ig_result.error)

    _decrypted_session = decrypt_field(meta.get("private_api_session_id"))
    has_web_session = bool(
        _decrypted_session
        and meta.get("private_api_csrf_token")
        and meta.get("private_api_ds_user_id")
    )
    has_sidecar_session = bool(_decrypted_session)
    web_result: PublishResult | None = None

    # 3. Web API (rupload_igphoto)
    if has_web_session:
        web_result = await _publish_instagram_via_web(account, text, post, media_paths)
        if web_result.success:
            return web_result

    # 4. Sidecar (aiograpi-rest)
    if has_sidecar_session:
        result = await _publish_instagram_via_sidecar(account, text, post, media_paths)
        if result.success:
            return result
        if not ("session" in (result.error or "").lower() and "expired" in (result.error or "").lower()):
            graph_token = await _resolve_ig_user_token(access_token, account, db)
            graph_result = await _publish_instagram_via_graph(
                graph_token, text, account, post, media_paths, storage_paths, db,
            )
            if graph_result.success or graph_result.ambiguous:
                return graph_result
            if web_result is not None and not web_result.success:
                return web_result
            return result
        graph_token = await _resolve_ig_user_token(access_token, account, db)
        return await _publish_instagram_via_graph(
            graph_token, text, account, post, media_paths, storage_paths, db,
        )

    # 5. Facebook Login Graph API (last resort)
    graph_token = await _resolve_ig_user_token(access_token, account, db)
    return await _publish_instagram_via_graph(
        graph_token, text, account, post, media_paths, storage_paths, db,
    )


async def _resolve_ig_user_token(
    access_token: str,
    account: SocialAccount,
    db: AsyncSession | None,
) -> str:
    """Resolve the correct user token for Instagram Graph API publishing.

    The Instagram Content Publishing API requires a **user** access token
    with ``instagram_content_publish`` scope, not a page token.  When an
    IG account was connected via Facebook OAuth, the stored token may be a
    page token.  In that case, look up the parent Facebook user account
    and use its token instead.
    """
    import logging

    log = logging.getLogger(__name__)
    meta = account.meta_data or {}
    # If connected via Instagram Business Login, the token is already a user token
    if meta.get("login_type") == "business_login":
        return access_token
    # If the account has a parent FB user account, use that user's token
    parent_account_id = getattr(account, "parent_account_id", None)
    team_id = getattr(account, "team_id", None)
    if parent_account_id and db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.id == account.parent_account_id)
        )
        parent = result.scalar_one_or_none()
        if parent and parent.platform == "facebook" and parent.account_type == "user":
            try:
                return decrypt_token(parent.access_token_enc)
            except Exception as exc:
                log.warning("Failed to decrypt parent FB user token: %s", exc)
        elif parent and parent.platform != "facebook":
            log.warning(
                "IG account parent is %s/%s, not a Facebook user — token may lack instagram_content_publish scope",
                parent.platform,
                parent.account_type,
            )
    # If no parent, try to find any active FB user account on the same team.
    # Use .first() instead of .scalar_one_or_none() to avoid MultipleResultsFound
    # if the team has multiple FB user accounts.
    if db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team_id,
                SocialAccount.platform == "facebook",
                SocialAccount.account_type == "user",
                SocialAccount.status == "active",
            ).limit(1)
        )
        fb_user = result.scalars().first()
        if fb_user:
            try:
                return decrypt_token(fb_user.access_token_enc)
            except Exception as exc:
                log.warning("Failed to decrypt fallback FB user token: %s", exc)
    # Fall back to the original token (may fail with permission error)
    log.warning("No FB user token found for IG account — falling back to stored token (may lack instagram_content_publish scope)")
    return access_token


async def _publish_tiktok(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    import os

    from app.services.tiktok_api import _video_chunk_plan

    def _tiktok_direct_post_kwargs(opts: dict) -> dict:
        """Map SocialAuto platform_specific.tiktok → TikTok Direct Post fields."""
        out: dict = {
            "brand_content_toggle": bool(opts.get("brand_content_toggle", False)),
            "brand_organic_toggle": bool(opts.get("brand_organic_toggle", False)),
        }
        for key in ("disable_duet", "disable_stitch", "disable_comment"):
            if key in opts and opts[key] is not None:
                out[key] = bool(opts[key])
        if opts.get("video_cover_timestamp_ms") is not None:
            try:
                out["video_cover_timestamp_ms"] = int(opts["video_cover_timestamp_ms"])
            except (TypeError, ValueError):
                pass
        if opts.get("is_aigc") is not None:
            out["is_aigc"] = bool(opts["is_aigc"])
        return out

    # Resolve public URLs for media using storage_paths (works with R2/MinIO)
    public_urls: list[str] = []
    for sp in (storage_paths or []):
        url = _media_public_url(sp)
        if url:
            public_urls.append(url)

    open_id = getattr(account, "meta_data", None) or {}
    if isinstance(open_id, dict):
        open_id = open_id.get("open_id") or account.account_id
    else:
        open_id = account.account_id
    client = TikTokAPIClient(access_token=access_token, open_id=open_id)

    # Detect video: single media file with a video extension.
    # Prefer FILE_UPLOAD when a local file is available (avoids TikTok
    # domain-verification requirement for PULL_FROM_URL).
    _VIDEO_EXTS = (".mp4", ".mov", ".webm")
    video_count = sum(1 for p in media_paths if p.lower().endswith(_VIDEO_EXTS))
    if video_count > 1:
        # Multi-video would fall into the photo branch and die there with an
        # opaque upstream error — reject it here with the actual rule.
        return PublishResult(
            success=False,
            skipped=True,
            error=(
                f"TikTok supports a single video or up to 35 photos per post — "
                f"got {video_count} videos. Split the post or drop the extra media."
            ),
        )
    local_video_path: str | None = None
    if len(media_paths) == 1 and media_paths[0].lower().endswith(_VIDEO_EXTS):
        if os.path.exists(media_paths[0]):
            local_video_path = media_paths[0]
    is_video = local_video_path is not None or (
        len(public_urls) == 1
        and bool(media_paths)
        and media_paths[0].lower().endswith(_VIDEO_EXTS)
    )

    tiktok_options = (post.platform_specific or {}).get("tiktok", {}) or {}
    # App audit approved (Sep 2026) — DIRECT_POST is available and publishes
    # directly. MEDIA_UPLOAD only drops a draft into the creator's mobile
    # inbox (needs the phone app to finish); kept as a per-post override.
    # DIRECT_POST publishes from SocialAuto only — TikTok does not expose its
    # commercial music catalog over the Content Posting API.
    publish_mode = str(tiktok_options.get("publish_mode", "DIRECT_POST")).upper()
    if publish_mode not in ("MEDIA_UPLOAD", "DIRECT_POST"):
        return PublishResult(success=False, error="TikTok publish_mode must be MEDIA_UPLOAD or DIRECT_POST")

    # Resume an in-flight publish (avoids burning another pending-share slot).
    # Before creator_info/media validation — resuming needs neither.
    existing_publish_id = str(tiktok_options.get("publish_id") or "").strip()
    if existing_publish_id:
        # A stored id predates the mode on the post — don't apply the new-post
        # default: inbox ids (v_inbox_*/v_upload_*) poll SEND_TO_USER_INBOX,
        # which is terminal only under MEDIA_UPLOAD.
        resume_mode = str(tiktok_options.get("publish_mode") or "").upper()
        if not resume_mode:
            resume_mode = (
                "MEDIA_UPLOAD"
                if existing_publish_id.startswith(("v_inbox", "v_upload"))
                else "DIRECT_POST"
            )
        logger.info(
            "[publishing] TikTok resuming status poll for existing publish_id=%s mode=%s",
            existing_publish_id,
            resume_mode,
        )
        return await _poll_tiktok_publish_status(
            client, existing_publish_id, resume_mode, account.username
        )

    # Brand account — public by default; creator_info still validates the
    # level against what the account actually allows.
    privacy_level = str(
        tiktok_options.get("privacy_level", "PUBLIC_TO_EVERYONE")
    ).upper()
    direct_kwargs = _tiktok_direct_post_kwargs(tiktok_options)
    if publish_mode == "DIRECT_POST":
        creator = await client.get_creator_info()
        privacy_options = (creator.get("data") or {}).get("privacy_level_options") or []
        if privacy_level not in privacy_options:
            return PublishResult(
                success=False,
                error=f"TikTok privacy_level must be one of: {', '.join(privacy_options)}",
            )

    # Photo posts always require PULL_FROM_URL (TikTok has no photo file upload).
    if not is_video and not public_urls:
        return PublishResult(
            success=False,
            error="No public media URLs available for TikTok (MEDIA_PUBLIC_BASE_URL not set or Cloudflare tunnel not running)",
        )

    # 1) Initialize the post
    upload_url: str | None = None
    planned_chunk_size: int | None = None
    video_size = 0
    if is_video and local_video_path:
        # FILE_UPLOAD path — validate media rules before init
        video_size = os.path.getsize(local_video_path)
        if video_size <= 0:
            return PublishResult(success=False, error="TikTok video file is empty")
        from app.services.tiktok_api import validate_tiktok_video_constraints

        media_error = validate_tiktok_video_constraints(local_video_path)
        if media_error:
            return PublishResult(success=False, error=media_error)
        planned_chunk_size, _ = _video_chunk_plan(video_size)
    elif is_video and not public_urls:
        return PublishResult(
            success=False,
            error="No public video URL for TikTok PULL_FROM_URL (verify domain or use local FILE_UPLOAD)",
        )

    async def _init(mode: str) -> dict:
        if is_video and local_video_path:
            if mode == "MEDIA_UPLOAD":
                return await client.init_video_upload(
                    source="FILE_UPLOAD",
                    video_size=video_size,
                    chunk_size=planned_chunk_size,
                )
            return await client.init_video_post(
                source="FILE_UPLOAD",
                title=text[:2200],
                privacy_level=privacy_level,
                video_size=video_size,
                chunk_size=planned_chunk_size,
                **direct_kwargs,
            )
        if is_video:
            if mode == "MEDIA_UPLOAD":
                return await client.init_video_upload(
                    source="PULL_FROM_URL",
                    video_url=public_urls[0],
                )
            return await client.init_video_post(
                source="PULL_FROM_URL",
                video_url=public_urls[0],
                title=text[:2200],
                privacy_level=privacy_level,
                **direct_kwargs,
            )
        if mode == "MEDIA_UPLOAD":
            return await client.init_photo_post_media_upload(
                photo_urls=public_urls[:35],
                title=text[:90],
                description=text[:4000],
            )
        photo_kwargs = {
            k: v
            for k, v in direct_kwargs.items()
            if k
            in (
                "disable_comment",
                "brand_content_toggle",
                "brand_organic_toggle",
                "is_aigc",
            )
        }
        if "auto_add_music" in tiktok_options:
            photo_kwargs["auto_add_music"] = bool(tiktok_options.get("auto_add_music"))
        if "photo_cover_index" in tiktok_options:
            try:
                photo_kwargs["photo_cover_index"] = int(tiktok_options["photo_cover_index"])
            except (TypeError, ValueError):
                pass
        return await client.init_photo_post(
            photo_urls=public_urls[:35],
            title=text[:90],
            privacy_level=privacy_level,
            description=text[:4000],
            **photo_kwargs,
        )

    try:
        init = await _init(publish_mode)
    except TikTokAPIError as exc:
        # Approved app, but the Content Posting visibility audit can lag the
        # app approval — fall back to an inbox draft rather than failing.
        if publish_mode == "DIRECT_POST" and _err_has(str(exc), _TT_UNAUDITED_MARKERS):
            logger.warning(
                "[publishing] TikTok DIRECT_POST gated (unaudited flag) — retrying as MEDIA_UPLOAD"
            )
            publish_mode = "MEDIA_UPLOAD"
            init = await _init(publish_mode)
        else:
            raise
    upload_url = init.get("data", {}).get("upload_url")

    publish_id = init.get("data", {}).get("publish_id")
    if not publish_id:
        error = init.get("error", {})
        clarified = _tiktok_clarify_error(
            f"{error.get('code', '')}: {error.get('message', error)}"
        )
        skip = _err_has(clarified, _TT_OWNERSHIP_MARKERS + _TT_UNAUDITED_MARKERS)
        return PublishResult(success=False, skipped=skip, error=f"TikTok init failed: {clarified}")

    # 1b) Upload video bytes if using FILE_UPLOAD
    if upload_url and local_video_path:
        content_type_map = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
        ext = os.path.splitext(local_video_path)[1].lower()
        content_type = content_type_map.get(ext, "video/mp4")
        with open(local_video_path, "rb") as fh:
            video_bytes = fh.read()
        await client.upload_video_file(
            upload_url=upload_url,
            video_bytes=video_bytes,
            content_type=content_type,
            chunk_size=planned_chunk_size,
        )
        logger.info(
            "[publishing] TikTok FILE_UPLOAD complete publish_id=%s bytes=%s",
            publish_id,
            len(video_bytes),
        )

    # 2) Poll for publish status (official statuses: PROCESSING_*,
    # SEND_TO_USER_INBOX, PUBLISH_COMPLETE, FAILED). Allow up to ~5 minutes —
    # TikTok can take a while after a successful 201 upload.
    return await _poll_tiktok_publish_status(
        client, publish_id, publish_mode, account.username
    )


async def _poll_tiktok_publish_status(
    client: TikTokAPIClient,
    publish_id: str,
    publish_mode: str,
    username: str | None,
    *,
    attempts: int = 60,
    interval_sec: float = 5.0,
) -> PublishResult:
    """Poll TikTok Get Post Status until a terminal state or timeout."""
    import asyncio as _asyncio

    def _meta(extra: dict | None = None) -> dict:
        tiktok_meta = {"publish_id": publish_id, "publish_mode": publish_mode}
        if extra:
            tiktok_meta.update(extra)
        return {"tiktok": tiktok_meta}

    last_status = ""
    last_uploaded_bytes: int | None = None
    for attempt in range(attempts):
        await _asyncio.sleep(interval_sec)
        status = await client.check_publish_status(publish_id)
        status_data = status.get("data", {}) or {}
        status_value = str(status_data.get("status") or "")
        last_status = status_value
        uploaded = status_data.get("uploaded_bytes")
        if uploaded is not None:
            last_uploaded_bytes = uploaded
        if status_value and status_value != "PROCESSING_UPLOAD":
            logger.info(
                "[publishing] TikTok status publish_id=%s attempt=%s status=%s",
                publish_id,
                attempt + 1,
                status_value,
            )
        if publish_mode == "MEDIA_UPLOAD" and status_value == "SEND_TO_USER_INBOX":
            # Inbox drafts have no public post URL yet — link the profile so
            # the notification still lands somewhere meaningful.
            return PublishResult(
                success=True,
                platform_post_id=publish_id,
                platform_url=(
                    f"https://www.tiktok.com/@{username}" if username else None
                ),
                platform_meta=_meta({"status": status_value}),
            )
        if status_value == "PUBLISH_COMPLETE":
            ids = status_data.get("publicaly_available_post_id") or []
            tt_post_id = str(ids[0]) if ids else None
            # Prefer public Display API video id for analytics; keep publish_id in meta.
            return PublishResult(
                success=True,
                platform_post_id=tt_post_id or publish_id,
                platform_url=(
                    f"https://www.tiktok.com/@{username}/video/{tt_post_id}"
                    if tt_post_id and username
                    else (f"https://www.tiktok.com/@{username}" if username else None)
                ),
                platform_meta=_meta(
                    {
                        "status": status_value,
                        **(
                            {"publicaly_available_post_id": tt_post_id}
                            if tt_post_id
                            else {}
                        ),
                    }
                ),
            )
        if status_value in ("FAILED", "CANCELLED"):
            fail_reason = status_data.get("fail_reason") or "unknown"
            clarified = _tiktok_clarify_error(str(fail_reason))
            skip = _err_has(clarified, _TT_OWNERSHIP_MARKERS + _TT_UNAUDITED_MARKERS)
            return PublishResult(
                success=False,
                skipped=skip,
                platform_post_id=publish_id,
                error=f"TikTok publish failed: {clarified}",
                platform_meta=_meta({"status": status_value, "fail_reason": fail_reason}),
            )

    action = "upload" if publish_mode == "MEDIA_UPLOAD" else "publish"
    detail = f"last_status={last_status or 'unknown'}"
    if last_uploaded_bytes is not None:
        detail += f", uploaded_bytes={last_uploaded_bytes}"
    return PublishResult(
        success=False,
        platform_post_id=publish_id,
        error=(
            f"TikTok {action} timeout (publish_id={publish_id}, {detail}). "
            "For MEDIA_UPLOAD, check the TikTok inbox or cancel via "
            "/api/v1/tiktok/accounts/{id}/publish/cancel. "
            "Videos must be ≥23 FPS, ≥360px, H.264/MP4 per TikTok media rules."
        ),
        platform_meta=_meta({"status": last_status or "timeout"}),
    )


# ── Threads ───────────────────────────────────────────────────────────────────

def _threads_media_kind(path: str) -> str:
    """Classify a media file as 'image' or 'video' by extension."""
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if ext in ("mp4", "mov", "webm"):
        return "video"
    return "image"


async def _publish_threads(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    """Publish to Threads using the actual ThreadsAPIClient.

    Supports text-only, single-image, single-video, and carousel posts
    (up to 20 mixed image/video items). The Threads API requires a
    two-step flow: create a media container, then publish it.

    Carousel items must be hosted at public URLs. The text/caption is
    attached to the parent carousel container (not individual items).
    """
    client = ThreadsAPIClient(access_token=access_token, user_id=account.account_id)

    # Resolve public URLs for each media asset (Threads requires URLs)
    media_urls: list[tuple[str, str]] = []  # (url, kind)
    if storage_paths:
        for sp in storage_paths[:20]:
            kind = _threads_media_kind(sp)
            # Threads image_url has the same JPEG/PNG constraint as Instagram Graph.
            url = _media_public_url(sp, force_jpeg=(kind == "image"))
            if url:
                media_urls.append((url, kind))

    try:
        if not media_urls:
            # Text-only post
            creation_id = await client.create_text_container(text=text[:500])
        elif len(media_urls) == 1:
            # Single media (image or video)
            url, kind = media_urls[0]
            if kind == "video":
                creation_id = await client.create_video_container(video_url=url, text=text[:500])
            else:
                creation_id = await client.create_image_container(image_url=url, text=text[:500])
        else:
            # Carousel: create each item, then combine into a carousel container
            child_ids: list[str] = []
            for url, kind in media_urls:
                if kind == "video":
                    cid = await client.create_video_container(video_url=url, is_carousel_item=True)
                else:
                    cid = await client.create_carousel_item(image_url=url, is_carousel_item=True)
                child_ids.append(cid)
            creation_id = await client.create_carousel_container(children_ids=child_ids, text=text[:500])

        # Threads processes containers async — publishing before FINISHED
        # fails with "Media ID is not available".
        await client.wait_for_container_ready(creation_id, timeout=60.0)
        media_id = await client.publish_container(creation_id)
    except ThreadsAPIError as exc:
        if exc.status_code == 403:
            return PublishResult(
                success=False,
                error="Threads API access denied. Ensure the app has threads_content_publish permission.",
            )
        return PublishResult(success=False, error=f"Threads publish failed: {exc}")
    except ValueError as exc:
        return PublishResult(success=False, error=f"Threads media error: {exc}")

    return PublishResult(
        success=True,
        platform_post_id=media_id,
        platform_url=f"https://www.threads.net/@{account.username}/post/{media_id}" if account.username else None,
    )


async def _publish_bluesky(
    access_token: str,  # the stored Bluesky app password (decrypted)
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    """Publish to Bluesky via the AT Protocol.

    A fresh ``createSession`` per publish avoids JWT lifecycle bookkeeping —
    app passwords are long-lived. Media is uploaded as blobs (raw bytes) and
    embedded: images → ``app.bsky.embed.images`` (≤4), a single mp4 →
    ``app.bsky.embed.video``. Bluesky does not auto-link URLs/mentions/
    hashtags — ``build_facets`` annotates them with UTF-8 byte offsets.
    Text is capped at 300 graphemes (we truncate to a safe 295).
    """
    from app.services.bluesky_api import (
        IMAGE_MIMES,
        MAX_IMAGES,
        BlueskyAPIError,
        BlueskyClient,
    )

    pds = (account.meta_data or {}).get("pds_url") or None
    client = BlueskyClient(
        account.username or "",
        access_token,
        pds_url=pds or "https://bsky.social",
    )
    try:
        await client.create_session()
    except BlueskyAPIError as exc:
        return PublishResult(
            success=False,
            error=f"Bluesky login failed ({exc.status_code}): {exc.response_text[:200]}",
        )

    bsky_text = text[:295]

    images: list[dict[str, Any]] = []
    video: dict[str, Any] | None = None
    try:
        from PIL import Image

        for path in media_paths:
            lower = path.lower()
            if lower.endswith(".mp4"):
                with open(path, "rb") as fh:
                    blob = await client.upload_blob(fh.read(), "video/mp4")
                video = {"blob": blob, "alt": post.title or ""}
                break  # one video per post; it replaces the image embed
            mime = mimetypes.guess_type(lower)[0] or "image/jpeg"
            if mime not in IMAGE_MIMES:
                mime = "image/jpeg"
            with open(path, "rb") as fh:
                data = fh.read()
            blob = await client.upload_blob(data, mime)
            try:
                with Image.open(path) as im:
                    w, h = im.size
                ar = {"width": w, "height": h}
            except Exception:
                ar = None
            images.append({"blob": blob, "alt": post.title or "", "aspectRatio": ar})
            if len(images) >= MAX_IMAGES:
                break

        result = await client.create_post(
            bsky_text,
            images=images or None,
            video=video,
        )
    except BlueskyAPIError as exc:
        return PublishResult(
            success=False,
            error=f"Bluesky publish failed ({exc.status_code}): {exc.response_text[:300]}",
        )

    at_uri = result.get("uri", "")
    # at://did:plc:…/app.bsky.feed.post/<rkey> → https://bsky.app/profile/<handle>/post/<rkey>
    rkey = at_uri.rsplit("/", 1)[-1] if at_uri else ""
    return PublishResult(
        success=True,
        platform_post_id=at_uri,
        platform_url=(
            f"https://bsky.app/profile/{account.username}/post/{rkey}"
            if account.username and rkey else None
        ),
    )
