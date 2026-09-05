"""Social media publishing pipeline.

Platform capabilities actually used per connected account:
  Twitter  (@TBaltzakis)           — text + images (up to 4), thread splitting
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

import base64
import dataclasses
import hashlib
import hmac
import io
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.content import MediaAsset, Post
from app.models.social_account import SocialAccount
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
from app.services.spellcheck import auto_correct
from app.services.threads_api import ThreadsAPIClient, ThreadsAPIError
from app.services.tiktok_api import TikTokAPIClient
from app.services.twitter_api import TwitterAPIClient, TwitterAPIError

logger = logging.getLogger(__name__)

_settings = get_settings()


@dataclasses.dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


# ── entry point ───────────────────────────────────────────────────────────────

async def publish_to_platform(
    account: SocialAccount,
    post: Post,
    db: AsyncSession,
) -> PublishResult:
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
    media_paths = await _resolve_media_paths(post, db)
    storage_paths = await _resolve_media_storage_paths(post, db)

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
    }
    fn = dispatch.get(account.platform)
    if fn is None:
        return PublishResult(success=False, error=f"Unsupported platform: {account.platform}")

    try:
        if account.platform == "instagram":
            return await fn(access_token, text, account, post, media_paths, storage_paths, db)
        return await fn(access_token, text, account, post, media_paths, storage_paths)
    except httpx.HTTPStatusError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:400]}")
    except LinkedInAPIError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.status_code}: {exc.response_text[:400]}")
    except Exception as exc:
        return PublishResult(success=False, error=str(exc))


# ── helpers ───────────────────────────────────────────────────────────────────

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
            print(f"[publishing] Failed to fetch {asset.storage_path} from {backend}: {exc}", flush=True)
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
        print(f"[publishing] Failed to fetch music {asset.storage_path} from {backend}: {exc}", flush=True)
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
    import tempfile
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
        print(f"[publishing] ffmpeg mix failed: {err}", flush=True)
        return video_path  # fall back to original
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


def _media_public_url(storage_path: str) -> str | None:
    """Build a publicly reachable URL for a local upload path.

    Priority:
    1. MEDIA_PUBLIC_BASE_URL + /api/v1/media/view?path=...
    2. /run/tunnel/url (Cloudflare tunnel) + /api/v1/media/view?path=...
    3. R2_PUBLIC_URL + storage_path (for R2-backed assets with relative keys)
    """
    base = (_settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        try:
            with open("/run/tunnel/url") as _f:
                base = _f.read().strip().rstrip("/")
        except OSError:
            pass
    if base:
        return f"{base}/api/v1/media/view?path={urllib.parse.quote(storage_path)}"
    # Fall back to R2 public URL for assets stored as relative R2 keys
    r2_base = (_settings.R2_PUBLIC_URL or "").rstrip("/")
    if r2_base and storage_path and not storage_path.startswith("/"):
        return f"{r2_base}/{storage_path}"
    return None


# ── Twitter ───────────────────────────────────────────────────────────────────
# Posts via Twitter API v2 (OAuth 2.0 user context).
# Media upload via v1.1 requires OAuth 1.0a; we sign using the app-level
# v1 credentials stored in settings (TWITTER_API_KEY / API_SECRET +
# ACCESS_TOKEN / ACCESS_TOKEN_SECRET).  These credentials belong to the
# same Twitter account (@TBaltzakis) that connected via OAuth 2.0 PKCE.

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


async def _twitter_upload_media(path: str) -> str | None:
    """Upload one image/GIF using Twitter v1.1 (OAuth 1.0a).  Returns media_id_string."""
    api_key = _settings.TWITTER_API_KEY
    api_secret = _settings.TWITTER_API_SECRET
    token = _settings.TWITTER_ACCESS_TOKEN
    token_secret = _settings.TWITTER_ACCESS_TOKEN_SECRET

    if not all([api_key, api_secret, token, token_secret]):
        print("[twitter] v1 credentials not configured — skipping media upload", flush=True)
        return None

    upload_url = "https://upload.twitter.com/1.1/media/upload.json"
    with open(path, "rb") as fh:
        img_bytes = fh.read()

    # OAuth 1.0a for multipart does NOT include file data in signature
    auth_header = _oauth1_auth_header(
        "POST", upload_url,
        api_key=api_key, api_secret=api_secret,
        token=token, token_secret=token_secret,
    )
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            upload_url,
            headers={"Authorization": auth_header},
            files={"media": ("media.png", img_bytes, "image/png")},
        )
        if resp.status_code == 200:
            mid = resp.json().get("media_id_string")
            if mid:
                print(f"[twitter] uploaded media {mid}", flush=True)
                return mid
        print(f"[twitter] media upload failed {resp.status_code}: {resp.text[:200]}", flush=True)
        return None


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


async def _publish_twitter(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None = None,
) -> PublishResult:
    """Publish to X/Twitter using TwitterAPIClient.

    Text + thread splitting via v2.  Image upload via v1.1 (OAuth 1.0a)
    when app-level credentials are configured; up to 4 images per tweet.
    """
    client = TwitterAPIClient(access_token=access_token)

    # Upload up to 4 images for the first tweet in the thread
    media_ids: list[str] = []
    if media_paths:
        image_paths = [p for p in media_paths[:4] if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]
        for img_path in image_paths:
            mid = await _twitter_upload_media(img_path)
            if mid:
                media_ids.append(mid)

    tweets = _split_thread(text)
    first_id: str | None = None
    last_id: str | None = None

    for i, chunk in enumerate(tweets):
        try:
            # Only attach media to the first tweet in the thread
            tweet_media_ids = media_ids if i == 0 else None
            result = await client.create_tweet(text=chunk, reply_tweet_id=last_id, media_ids=tweet_media_ids)
        except TwitterAPIError as exc:
            if exc.status_code == 402:
                return PublishResult(
                    success=False,
                    error=(
                        "X free tier monthly write quota exhausted (1,500 tweets/month). "
                        "Quota resets on your billing date. Upgrade to X Basic ($100/month) "
                        "for 3,000 tweets + media upload."
                    ),
                )
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

    # Detect PDF files in the media list — these are pre-built carousels
    # that should be uploaded as LinkedIn documents, not images.
    pdf_paths = [p for p in media_paths if p.lower().endswith(".pdf")]
    image_paths = [p for p in media_paths if not p.lower().endswith(".pdf")]

    if pdf_paths:
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
            "https://graph.facebook.com/me/accounts",
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

    try:
        # Determine post type
        image_paths = [p for p in media_paths if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]
        video_paths = [p for p in media_paths if p.lower().endswith((".mp4", ".mov", ".webm", ".avi"))]

        if video_paths:
            # Video post
            with open(video_paths[0], "rb") as fh:
                video_bytes = fh.read()
            result = await client.post_video(video_bytes, message=text)
        elif image_paths:
            # Photo post (single or multi)
            images = []
            for p in image_paths[:10]:
                with open(p, "rb") as fh:
                    img_b64 = base64.b64encode(fh.read()).decode()
                images.append({"image_base64": img_b64, "filename": os.path.basename(p)})
            result = await client.post_photo(images=images, message=text)
        elif post.link_url:
            # Link post
            result = await client.post_link(url=post.link_url, message=text)
        else:
            # Text-only post
            result = await client.post_text(message=text)

        post_url = result.get("url")
        post_id = result.get("post_id") or (post_url.split("/")[-1] if post_url else None)
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
    # Personal profile (type=user) with a browser session → use sidecar
    if account.account_type == "user" and _has_facebook_browser_session(account):
        result = await _publish_facebook_via_sidecar(account, text, post, media_paths)
        if result.success:
            return result
        # If sidecar fails, fall through to Graph API below

    page_id = account.account_id
    # access_token is stored as the page token from OAuth callback.
    # Fall back to dynamic lookup for accounts connected before this fix.
    page_token = await _facebook_page_token(access_token, page_id)

    # Text/link posts go through the real FacebookAPIClient.
    # Photo albums still use Facebook's unpublished upload flow (file bytes).
    fb_client = FacebookAPIClient(access_token=page_token, page_id=page_id)

    if not media_paths:
        result = await fb_client.create_post(message=text, link=post.link_url)
        fb_post_id = result.get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=fb_post_id,
            platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
        )

    graph_base = "https://graph.facebook.com/v20.0"
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
            r.raise_for_status()
            fb_post_id = r.json().get("id", "")
        else:
            # Photo uploads all failed — fall through to text post
            result = await fb_client.create_post(message=text, link=post.link_url)
            fb_post_id = result.get("id", "")

    return PublishResult(
        success=True,
        platform_post_id=fb_post_id,
        platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
    )


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
) -> list[str]:
    """Resolve public URLs for media assets.

    Priority:
    1. platform_specific.instagram.image_urls (manual override)
    2. MEDIA_PUBLIC_BASE_URL + /api/v1/media/view?path=...
    3. Empty list (image posting not available)
    """
    override: list[str] = (post.platform_specific or {}).get("instagram", {}).get("image_urls", [])
    single: str | None = (post.platform_specific or {}).get("instagram", {}).get("image_url")
    if single and not override:
        override = [single]
    if override:
        return override

    urls: list[str] = []
    for sp in storage_paths:
        url = _media_public_url(sp)
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
    meta = account.meta_data or {}
    session_id = meta.get("private_api_session_id")
    csrf_token = meta.get("private_api_csrf_token")
    ds_user_id = meta.get("private_api_ds_user_id")
    if not session_id or not csrf_token or not ds_user_id:
        return PublishResult(
            success=False,
            error="Instagram web API session incomplete (need sessionid, csrftoken, ds_user_id).",
        )

    if not media_paths:
        return PublishResult(
            success=False,
            error="Instagram requires at least one image or video. Set media on the post.",
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
        "X-IG-App-ID": "1217981644879628",
        "x-csrftoken": csrf_token,
        "Cookie": cookie_header,
    }
    caption = text[:2200]

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: Upload each photo via rupload_igphoto
        upload_ids: list[str] = []
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

        # Step 2: Configure the media (create the post)
        configure_headers = {
            **base_headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.instagram.com/create/details/",
            "Origin": "https://www.instagram.com",
        }

        if is_carousel:
            # Carousel: configure with children_metadata
            children_metadata = json.dumps([
                {"upload_id": uid} for uid in upload_ids
            ])
            configure_data = {
                "caption": caption,
                "children_metadata": children_metadata,
                "source_type": "1",
                "device_id": "android-e021b636049dc0e9",
            }
            configure_url = "https://www.instagram.com/create/configure_sidecar/"
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
            configure_url = "https://www.instagram.com/create/configure/"

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

        configure_resp = resp2.json()
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
    meta = account.meta_data or {}
    session_id = meta.get("private_api_session_id")
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

    # No media → text-only is not supported by Instagram
    if not media_paths:
        return PublishResult(
            success=False,
            error="Instagram requires at least one image or video. Set media on the post.",
        )

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


async def _publish_instagram_via_graph(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
    storage_paths: list[str] | None,
) -> PublishResult:
    """Publish via the Instagram Graph API (fallback)."""
    ig_user_id = account.account_id

    # Detect fallback accounts (FB user ID used because no IG Business account found)
    meta = account.meta_data or {}
    if meta.get("account_type", "person") == "person" and not meta.get("ig_business_id"):
        async with httpx.AsyncClient(timeout=10.0) as probe:
            r = await probe.get(
                f"https://graph.facebook.com/v20.0/{ig_user_id}",
                params={"fields": "account_type", "access_token": access_token},
            )
            data = r.json()
            if data.get("account_type") not in ("BUSINESS", "CREATOR", None):
                return PublishResult(
                    success=False,
                    error=(
                        "Instagram posting requires a Business or Creator account linked to a Facebook Page. "
                        "In Instagram app: Settings → Account → Switch to Professional Account."
                    ),
                )

    image_urls = await _instagram_public_urls(storage_paths or [], post)
    caption = text[:2200]

    if not image_urls:
        if media_paths:
            return PublishResult(
                success=False,
                error=(
                    "Instagram requires publicly accessible image URLs. "
                    "Set MEDIA_PUBLIC_BASE_URL in .env to a public-facing URL "
                    "(e.g. an ngrok tunnel or Cloudflare Tunnel) and restart the API."
                ),
            )
        return PublishResult(
            success=False,
            error="Instagram requires at least one image. Set an image on the post.",
        )

    is_business_login = (account.meta_data or {}).get("login_type") == "business_login"
    client = InstagramAPIClient(
        access_token=access_token,
        ig_user_id=ig_user_id,
        use_business_login_api=is_business_login,
    )
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
        media_id = await client.publish_container(creation_id)
    except InstagramAPIError as exc:
        return PublishResult(
            success=False,
            error=f"Instagram publish failed: {exc}",
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
    username = (_settings.INSTAGRAM_USERNAME or "").strip()
    password = (_settings.INSTAGRAM_PASSWORD or "").strip()
    if not username or not password:
        return PublishResult(
            success=False,
            error="INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD are not set.",
        )

    if not media_paths:
        return PublishResult(
            success=False,
            error="Instagram requires at least one image or video.",
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
        1. **instagrapi** — direct Python private mobile API. Requires
           INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in env. Works for any
           account type without Meta App Review.
        2. **Web API** (rupload_igphoto) — uses browser sessionid cookie
           directly against www.instagram.com. Requires private_api_session_id,
           private_api_csrf_token, private_api_ds_user_id in meta_data.
        3. **Sidecar** (aiograpi-rest private mobile API) — fallback for
           video uploads or when the above paths are unavailable.
        4. **Graph API** — last resort (requires Meta App Review for
           instagram_content_publish permission).
    """
    meta = account.meta_data or {}

    # 1. instagrapi (direct Python) — primary path when credentials are set
    if (_settings.INSTAGRAM_USERNAME or "").strip() and (_settings.INSTAGRAM_PASSWORD or "").strip():
        ig_result = await _publish_instagram_via_instagrapi(text, post, media_paths)
        if ig_result.success:
            return ig_result
        logger.warning("instagrapi path failed: %s — trying fallback paths", ig_result.error)

    has_web_session = bool(
        meta.get("private_api_session_id")
        and meta.get("private_api_csrf_token")
        and meta.get("private_api_ds_user_id")
    )
    has_sidecar_session = bool(meta.get("private_api_session_id"))
    web_result: PublishResult | None = None

    # 2. Web API (rupload_igphoto)
    if has_web_session:
        web_result = await _publish_instagram_via_web(account, text, post, media_paths)
        if web_result.success:
            return web_result

    # 3. Sidecar (aiograpi-rest)
    if has_sidecar_session:
        result = await _publish_instagram_via_sidecar(account, text, post, media_paths)
        if result.success:
            return result
        if not ("session" in (result.error or "").lower() and "expired" in (result.error or "").lower()):
            graph_token = await _resolve_ig_user_token(access_token, account, db)
            graph_result = await _publish_instagram_via_graph(
                graph_token, text, account, post, media_paths, storage_paths,
            )
            if graph_result.success:
                return graph_result
            if web_result is not None and not web_result.success:
                return web_result
            return result
        graph_token = await _resolve_ig_user_token(access_token, account, db)
        return await _publish_instagram_via_graph(
            graph_token, text, account, post, media_paths, storage_paths,
        )

    # 4. Graph API (last resort)
    graph_token = await _resolve_ig_user_token(access_token, account, db)
    return await _publish_instagram_via_graph(
        graph_token, text, account, post, media_paths, storage_paths,
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
    import asyncio as _asyncio
    import os

    # Resolve public URLs for media using storage_paths (works with R2/MinIO)
    public_urls: list[str] = []
    for sp in (storage_paths or []):
        url = _media_public_url(sp)
        if url:
            public_urls.append(url)

    client = TikTokAPIClient(access_token=access_token, open_id=account.account_id)

    # Detect video: single media file with a video extension.
    # Prefer FILE_UPLOAD when a local file is available (avoids TikTok
    # domain-verification requirement for PULL_FROM_URL).
    local_video_path: str | None = None
    if len(media_paths) == 1 and media_paths[0].lower().endswith((".mp4", ".mov", ".webm")):
        if os.path.exists(media_paths[0]):
            local_video_path = media_paths[0]
    is_video = local_video_path is not None or (
        len(public_urls) == 1 and media_paths and media_paths[0].lower().endswith((".mp4", ".mov", ".webm"))
    )

    tiktok_options = (post.platform_specific or {}).get("tiktok", {})
    publish_mode = str(tiktok_options.get("publish_mode", "MEDIA_UPLOAD")).upper()
    if publish_mode not in ("MEDIA_UPLOAD", "DIRECT_POST"):
        return PublishResult(success=False, error="TikTok publish_mode must be MEDIA_UPLOAD or DIRECT_POST")

    privacy_level = str(tiktok_options.get("privacy_level", "SELF_ONLY")).upper()
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
    if is_video and local_video_path:
        # FILE_UPLOAD path — read the video bytes and upload directly
        video_size = os.path.getsize(local_video_path)
        if publish_mode == "MEDIA_UPLOAD":
            init = await client.init_video_upload(
                source="FILE_UPLOAD",
                video_size=video_size,
            )
        else:
            init = await client.init_video_post(
                source="FILE_UPLOAD",
                title=text[:2200],
                privacy_level=privacy_level,
                video_size=video_size,
            )
        upload_url = init.get("data", {}).get("upload_url")
    elif is_video and publish_mode == "MEDIA_UPLOAD":
        init = await client.init_video_upload(
            source="PULL_FROM_URL",
            video_url=public_urls[0],
        )
    elif is_video:
        init = await client.init_video_post(
            source="PULL_FROM_URL",
            video_url=public_urls[0],
            title=text[:2200],
            privacy_level=privacy_level,
        )
    elif publish_mode == "MEDIA_UPLOAD":
        init = await client.init_photo_post_media_upload(
            photo_urls=public_urls[:35],
            title=text[:90],
            description=text[:4000],
        )
    else:
        init = await client.init_photo_post(
            photo_urls=public_urls[:35],
            title=text[:90],
            privacy_level=privacy_level,
        )

    publish_id = init.get("data", {}).get("publish_id")
    if not publish_id:
        error = init.get("error", {})
        return PublishResult(success=False, error=f"TikTok init failed: {error}")

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
        )

    # 2) Poll for publish status (up to 90s)
    for _ in range(18):
        await _asyncio.sleep(5)
        status = await client.check_publish_status(publish_id)
        status_data = status.get("data", {})
        status_value = status_data.get("status", "")
        if publish_mode == "MEDIA_UPLOAD" and status_value == "SEND_TO_USER_INBOX":
            return PublishResult(success=True, platform_post_id=publish_id)
        if status_value == "PUBLISH_COMPLETE":
            tt_post_id = status_data.get("publicaly_available_post_id", [None])[0]
            return PublishResult(
                success=True,
                platform_post_id=publish_id,
                platform_url=f"https://www.tiktok.com/@{account.username}/video/{tt_post_id}" if tt_post_id else None,
            )
        if status_value in ("FAILED", "CANCELLED"):
            fail_reason = status_data.get("fail_reason", "unknown")
            return PublishResult(success=False, error=f"TikTok publish failed: {fail_reason}")

    action = "upload" if publish_mode == "MEDIA_UPLOAD" else "publish"
    return PublishResult(success=False, error=f"TikTok {action} timeout (publish_id={publish_id})")


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
            url = _media_public_url(sp)
            if url:
                media_urls.append((url, _threads_media_kind(sp)))

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
