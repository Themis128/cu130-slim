"""Social media publishing pipeline.

Platform capabilities actually used per connected account:
  Twitter  (@TBaltzakis)           — text + images (up to 4), thread splitting
  Facebook (personal user token)   — pages managed by user: text + photos + multi-photo
  Instagram (Business/Creator)     — single image + carousel (requires public image URLs)
  LinkedIn  (person + org page)    — text / single image / multi-image / PDF carousel
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import io
import os
import secrets
import time
import urllib.parse
from urllib.parse import unquote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.content import MediaAsset, Post
from app.models.social_account import SocialAccount

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
    media_paths = await _resolve_media_paths(post, db)

    dispatch = {
        "twitter": _publish_twitter,
        "linkedin": _publish_linkedin,
        "facebook": _publish_facebook,
        "instagram": _publish_instagram,
    }
    fn = dispatch.get(account.platform)
    if fn is None:
        return PublishResult(success=False, error=f"Unsupported platform: {account.platform}")

    try:
        return await fn(access_token, text, account, post, media_paths)
    except httpx.HTTPStatusError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:400]}")
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
    return paths


def _build_post_text(post: Post, platform: str) -> str:
    parts: list[str] = []
    if post.content_text:
        parts.append(post.content_text)

    override = (post.platform_specific or {}).get(platform, {})
    if override.get("content_text"):
        parts = [override["content_text"]]

    if post.hashtags:
        tags = " ".join(f"#{t.lstrip('#')}" for t in post.hashtags)
        if platform in ("twitter", "instagram"):
            parts.append(tags)

    if post.link_url and platform != "twitter":
        parts.append(post.link_url)

    return "\n\n".join(p for p in parts if p)


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

    Uses MEDIA_PUBLIC_BASE_URL from settings, or reads /run/tunnel/url written
    by the cloudflared sidecar (auto-updated on each tunnel restart).
    """
    base = (_settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        try:
            with open("/run/tunnel/url") as _f:
                base = _f.read().strip().rstrip("/")
        except OSError:
            return None
    if not base:
        return None
    return f"{base}/api/v1/media/view?path={urllib.parse.quote(storage_path)}"


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
) -> PublishResult:
    # Free tier does not support media upload (v1.1 requires Basic+); skip silently.
    headers_v2 = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    tweets = _split_thread(text)
    first_id: str | None = None
    last_id: str | None = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, chunk in enumerate(tweets):
            payload: dict = {"text": chunk}
            if last_id:
                payload["reply"] = {"in_reply_to_tweet_id": last_id}
            resp = await client.post(
                "https://api.twitter.com/2/tweets",
                headers=headers_v2,
                json=payload,
            )
            if resp.status_code == 402:
                return PublishResult(
                    success=False,
                    error="X free tier monthly write quota exhausted (1,500 tweets/month). Quota resets on your billing date. Upgrade to X Basic ($100/month) for 3,000 tweets + media upload.",
                )
            resp.raise_for_status()
            tid = resp.json().get("data", {}).get("id", "")
            if first_id is None:
                first_id = tid
            last_id = tid

    return PublishResult(
        success=True,
        platform_post_id=first_id,
        platform_url=f"https://twitter.com/{account.username}/status/{first_id}" if first_id else None,
    )


# ── LinkedIn ──────────────────────────────────────────────────────────────────

def _linkedin_author_urn(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    account_type = (meta.get("account_type") or "person").lower()
    if account_type in ("organization", "company", "page"):
        return f"urn:li:organization:{account.account_id}"
    if meta.get("author_urn"):
        return str(meta["author_urn"])
    return f"urn:li:person:{account.account_id}"


async def _publish_linkedin(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    author_urn = _linkedin_author_urn(account)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": "202608",
    }

    # Carousel / document: 2+ images → PDF
    if len(media_paths) >= 2:
        post_title = (post.content_text or "Carousel")[:80]
        pdf_bytes = _images_to_pdf(media_paths, title=post_title)
        return await _publish_linkedin_document(access_token, text, author_urn, pdf_bytes, headers, post_title)

    # Single image
    if len(media_paths) == 1:
        return await _publish_linkedin_multi_image(access_token, text, author_urn, media_paths, headers)

    # Text-only / link post
    payload: dict = {
        "author": author_urn,
        "commentary": text[:3000],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    # Attach link preview when available
    if post.link_url:
        payload["content"] = {
            "article": {
                "source": post.link_url,
                "title": (post.link_preview_override or {}).get("title", ""),
                "description": (post.link_preview_override or {}).get("description", ""),
            }
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        resp.raise_for_status()
        post_id = unquote(resp.headers.get("x-restli-id", "") or "")
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
        )


async def _publish_linkedin_document(
    access_token: str,
    text: str,
    author_urn: str,
    pdf_bytes: bytes,
    headers: dict,
    title: str = "Carousel",
) -> PublishResult:
    import asyncio
    from urllib.parse import quote

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Initialize upload
        reg = await client.post(
            "https://api.linkedin.com/rest/documents?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": author_urn}},
        )
        reg.raise_for_status()
        val = reg.json()["value"]
        upload_url = val["uploadUrl"]
        document_urn = val["document"]

        # 2. Upload PDF
        up = await client.put(
            upload_url,
            headers={"Authorization": f"Bearer {access_token}"},
            content=pdf_bytes,
        )
        up.raise_for_status()

        # 3. Poll for AVAILABLE
        encoded_urn = quote(document_urn, safe="")
        last_status = None
        for _ in range(30):
            await asyncio.sleep(2)
            st = await client.get(
                f"https://api.linkedin.com/rest/documents/{encoded_urn}",
                headers=headers,
            )
            if st.status_code >= 400:
                continue
            last_status = st.json().get("status")
            if last_status == "AVAILABLE":
                break
            if last_status == "PROCESSING_FAILED":
                return PublishResult(success=False, error="LinkedIn document processing failed")
        else:
            return PublishResult(success=False, error=f"Document processing timeout (status={last_status})")

        # 4. Create post
        payload = {
            "author": author_urn,
            "commentary": text[:3000],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {
                "media": {
                    "title": title[:200],
                    "id": document_urn,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        pr = await client.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers,
            json=payload,
        )
        pr.raise_for_status()
        post_id = unquote(pr.headers.get("x-restli-id", "") or "")
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
        )


async def _publish_linkedin_multi_image(
    access_token: str,
    text: str,
    author_urn: str,
    media_paths: list[str],
    headers: dict,
) -> PublishResult:
    import asyncio
    from urllib.parse import quote

    image_urns: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for path in media_paths[:20]:
            with open(path, "rb") as fh:
                img_bytes = fh.read()
            init = await client.post(
                "https://api.linkedin.com/rest/images?action=initializeUpload",
                headers=headers,
                json={"initializeUploadRequest": {"owner": author_urn}},
            )
            init.raise_for_status()
            value = init.json()["value"]
            up = await client.put(
                value["uploadUrl"],
                headers={"Authorization": f"Bearer {access_token}"},
                content=img_bytes,
            )
            up.raise_for_status()
            encoded = quote(value["image"], safe="")
            for _ in range(20):
                await asyncio.sleep(1)
                st = await client.get(f"https://api.linkedin.com/rest/images/{encoded}", headers=headers)
                if st.status_code < 400 and st.json().get("status") == "AVAILABLE":
                    break
            image_urns.append(value["image"])

        if len(image_urns) >= 2:
            content: dict = {
                "multiImage": {
                    "images": [{"id": urn, "altText": f"Slide {i + 1}"} for i, urn in enumerate(image_urns)]
                }
            }
        else:
            content = {"media": {"id": image_urns[0], "title": "Image"}}

        payload = {
            "author": author_urn,
            "commentary": text[:3000],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": content,
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        pr = await client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        pr.raise_for_status()
        post_id = unquote(pr.headers.get("x-restli-id", "") or "")
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
        )


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


async def _publish_facebook(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    page_id = account.account_id
    # access_token is stored as the page token from OAuth callback.
    # Fall back to dynamic lookup for accounts connected before this fix.
    page_token = await _facebook_page_token(access_token, page_id)

    graph_base = "https://graph.facebook.com/v20.0"
    async with httpx.AsyncClient(timeout=90.0) as client:
        if media_paths:
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
                fb_post_id = ""

            if fb_post_id:
                return PublishResult(
                    success=True,
                    platform_post_id=fb_post_id,
                    platform_url=f"https://www.facebook.com/{fb_post_id}",
                )

        # Text-only (or photo fallback)
        params: dict = {"message": text, "access_token": page_token}
        if post.link_url:
            params["link"] = post.link_url
        r = await client.post(f"{graph_base}/{page_id}/feed", params=params)
        r.raise_for_status()
        fb_post_id = r.json().get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=fb_post_id,
            platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
        )


# ── Instagram ─────────────────────────────────────────────────────────────────
# Requires an Instagram Business/Creator account linked to a Facebook Page.
# Images must be at publicly reachable URLs (Instagram servers fetch them).
# Set MEDIA_PUBLIC_BASE_URL in .env to a public base (ngrok, Cloudflare Tunnel,
# or any public host) to enable image-based posts.

async def _instagram_public_urls(
    media_paths: list[str],
    post: Post,
) -> list[str]:
    """Resolve public URLs for local media paths.

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
    for path in media_paths:
        # Normalise to storage_path (relative inside upload_dir)
        upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
        rel = path.replace(upload_dir, "").lstrip("/") if path.startswith(upload_dir) else path
        url = _media_public_url(rel)
        if url:
            urls.append(url)
    return urls


async def _publish_instagram(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    ig_user_id = account.account_id

    # Detect fallback accounts (FB user ID used because no IG Business account found)
    # account_type will be "person" and scopes suggest this is a fallback
    meta = account.meta_data or {}
    if meta.get("account_type", "person") == "person" and not meta.get("ig_business_id"):
        # Check via Graph API if this ID actually has IG content publishing
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

    image_urls = await _instagram_public_urls(media_paths, post)
    caption = text[:2200]

    if not image_urls:
        if media_paths:
            # Media exists but can't generate public URL
            return PublishResult(
                success=False,
                error=(
                    "Instagram requires publicly accessible image URLs. "
                    "Set MEDIA_PUBLIC_BASE_URL in .env to a public-facing URL "
                    "(e.g. an ngrok tunnel or Cloudflare Tunnel) and restart the API."
                ),
            )
        # Text-only: Instagram doesn't support text-only posts via Graph API
        return PublishResult(
            success=False,
            error="Instagram requires at least one image. Set an image on the post.",
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        if len(image_urls) == 1:
            # Single image post
            cr = await client.post(
                f"https://graph.facebook.com/v20.0/{ig_user_id}/media",
                params={
                    "image_url": image_urls[0],
                    "caption": caption,
                    "access_token": access_token,
                },
            )
            cr.raise_for_status()
            creation_id = cr.json().get("id")
        else:
            # Carousel (2–10 images)
            child_ids: list[str] = []
            for url in image_urls[:10]:
                cr = await client.post(
                    f"https://graph.facebook.com/v20.0/{ig_user_id}/media",
                    params={
                        "image_url": url,
                        "is_carousel_item": "true",
                        "access_token": access_token,
                    },
                )
                if cr.status_code == 200:
                    cid = cr.json().get("id")
                    if cid:
                        child_ids.append(cid)

            if not child_ids:
                return PublishResult(success=False, error="No Instagram carousel items could be created")

            cr = await client.post(
                f"https://graph.facebook.com/v20.0/{ig_user_id}/media",
                params={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": caption,
                    "access_token": access_token,
                },
            )
            cr.raise_for_status()
            creation_id = cr.json().get("id")

        if not creation_id:
            return PublishResult(success=False, error="Instagram media container creation returned no ID")

        # Publish
        pub = await client.post(
            f"https://graph.facebook.com/v20.0/{ig_user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": access_token},
        )
        pub.raise_for_status()
        media_id = pub.json().get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=media_id,
            platform_url=f"https://www.instagram.com/p/{media_id}" if media_id else None,
        )
