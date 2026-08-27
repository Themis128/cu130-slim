import dataclasses
import io
import os

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.content import MediaAsset, Post
from app.models.social_account import SocialAccount


@dataclasses.dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


async def publish_to_platform(
    account: SocialAccount,
    post: Post,
    db: AsyncSession,
) -> PublishResult:
    try:
        access_token = decrypt_token(account.access_token_enc)
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
        return PublishResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:300]}")
    except Exception as exc:
        return PublishResult(success=False, error=str(exc))


async def _resolve_media_paths(post: Post, db: AsyncSession) -> list[str]:
    """Return local filesystem paths for all media assets attached to this post."""
    if not post.media_ids:
        return []
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id.in_(post.media_ids))
    )
    assets = result.scalars().all()
    # preserve order (normalize UUID types — asyncpg UUID vs uuid.UUID)
    id_order = {str(mid): i for i, mid in enumerate(post.media_ids)}
    assets_sorted = sorted(assets, key=lambda a: id_order.get(str(a.id), 999))
    upload_dir = os.environ.get("UPLOAD_DIR", "/app/uploads")
    paths: list[str] = []
    missing: list[str] = []
    for asset in assets_sorted:
        path = asset.storage_path
        if not path:
            missing.append(str(asset.id))
            continue
        if not os.path.isabs(path):
            path = os.path.join(upload_dir, path)
        if os.path.exists(path):
            paths.append(path)
        else:
            missing.append(path)
    if post.media_ids and not paths:
        print(
            f"[publishing] media_ids={len(post.media_ids)} resolved=0 "
            f"assets={len(assets)} missing={missing[:5]}",
            flush=True,
        )
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

    if post.link_url:
        parts.append(post.link_url)

    return "\n\n".join(p for p in parts if p)


def _images_to_pdf(image_paths: list[str]) -> bytes:
    """Combine PNG/JPEG files into a single PDF using Pillow."""
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


# ── Twitter ───────────────────────────────────────────────────────────────────

async def _publish_twitter(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    media_ids: list[str] = []

    # Upload up to 4 images via v1.1 media upload
    for path in media_paths[:4]:
        with open(path, "rb") as f:
            img_bytes = f.read()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://upload.twitter.com/1.1/media/upload.json",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"media": ("slide.png", img_bytes, "image/png")},
            )
            if resp.status_code == 200:
                mid = resp.json().get("media_id_string")
                if mid:
                    media_ids.append(mid)

    payload: dict = {"text": text[:280]}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.twitter.com/2/tweets",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        tweet_id = data.get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=tweet_id,
            platform_url=f"https://twitter.com/i/web/status/{tweet_id}" if tweet_id else None,
        )


# ── LinkedIn ──────────────────────────────────────────────────────────────────

def _linkedin_author_urn(account: SocialAccount) -> str:
    """Return person or organization author URN based on account metadata."""
    meta = account.meta_data or {}
    account_type = (meta.get("account_type") or "person").lower()
    if account_type in ("organization", "company", "page"):
        return f"urn:li:organization:{account.account_id}"
    # Allow full URN stored in metadata
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

    if len(media_paths) >= 2:
        # Organic multi-slide: PDF document via Documents + Posts APIs
        pdf_bytes = _images_to_pdf(media_paths)
        return await _publish_linkedin_document(access_token, text, author_urn, pdf_bytes, headers)

    if len(media_paths) == 1:
        return await _publish_linkedin_multi_image(access_token, text, author_urn, media_paths, headers)

    # Text-only post via Posts API
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
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        resp.raise_for_status()
        post_id = resp.headers.get("x-restli-id", "")
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
) -> PublishResult:
    from urllib.parse import quote

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Step 1: Initialize document upload
        reg_resp = await client.post(
            "https://api.linkedin.com/rest/documents?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": author_urn}},
        )
        reg_resp.raise_for_status()
        reg_data = reg_resp.json()
        upload_url = reg_data["value"]["uploadUrl"]
        document_urn = reg_data["value"]["document"]

        # Step 2: Upload PDF bytes
        upload_resp = await client.put(
            upload_url,
            headers={"Authorization": f"Bearer {access_token}"},
            content=pdf_bytes,
        )
        upload_resp.raise_for_status()

        # Step 3: Poll until AVAILABLE (URN must be fully URL-encoded for Rest.li)
        import asyncio

        encoded_urn = quote(document_urn, safe="")
        max_retries = 30
        last_status = None
        for i in range(max_retries):
            await asyncio.sleep(2)
            status_resp = await client.get(
                f"https://api.linkedin.com/rest/documents/{encoded_urn}",
                headers=headers,
            )
            if status_resp.status_code >= 400:
                print(
                    f"[publishing] document status HTTP {status_resp.status_code}: {status_resp.text[:300]}",
                    flush=True,
                )
                if i == max_retries - 1:
                    return PublishResult(
                        success=False,
                        error=f"Document status check failed: HTTP {status_resp.status_code} {status_resp.text[:200]}",
                    )
                continue
            status_data = status_resp.json()
            last_status = status_data.get("status")
            if last_status == "AVAILABLE":
                break
            if last_status == "PROCESSING_FAILED":
                return PublishResult(success=False, error="LinkedIn document processing failed")
            if i == max_retries - 1:
                return PublishResult(
                    success=False,
                    error=f"Document processing timeout (last status={last_status})",
                )

        # Step 4: Create post with document via Posts API (ugcPosts is deprecated)
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
                    "title": "cloudless.gr carousel.pdf",
                    "id": document_urn,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        post_resp = await client.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers,
            json=payload,
        )
        post_resp.raise_for_status()
        post_id = post_resp.headers.get("x-restli-id", "")
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
    """Upload image(s) via Images API and create a Posts API media/multiImage post."""
    import asyncio
    from urllib.parse import quote

    image_urns: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for path in media_paths[:20]:
            with open(path, "rb") as handle:
                img_bytes = handle.read()
            init = await client.post(
                "https://api.linkedin.com/rest/images?action=initializeUpload",
                headers=headers,
                json={"initializeUploadRequest": {"owner": author_urn}},
            )
            init.raise_for_status()
            value = init.json()["value"]
            upload_url = value["uploadUrl"]
            image_urn = value["image"]
            up = await client.put(
                upload_url,
                headers={"Authorization": f"Bearer {access_token}"},
                content=img_bytes,
            )
            up.raise_for_status()
            encoded = quote(image_urn, safe="")
            for _ in range(20):
                await asyncio.sleep(1)
                st = await client.get(f"https://api.linkedin.com/rest/images/{encoded}", headers=headers)
                if st.status_code < 400 and st.json().get("status") == "AVAILABLE":
                    break
            image_urns.append(image_urn)

        if len(image_urns) >= 2:
            content = {
                "multiImage": {
                    "images": [{"id": urn, "altText": f"Slide {i + 1}"} for i, urn in enumerate(image_urns)]
                }
            }
        else:
            content = {"media": {"id": image_urns[0], "title": "Slide"}}

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
        post_resp = await client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        post_resp.raise_for_status()
        post_id = post_resp.headers.get("x-restli-id", "")
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
        )


# ── Facebook ──────────────────────────────────────────────────────────────────

async def _publish_facebook(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    page_id = account.account_id

    async with httpx.AsyncClient(timeout=60.0) as client:
        if media_paths:
            # Upload each photo, collect IDs, then publish as multi-photo post
            photo_ids: list[str] = []
            for path in media_paths[:10]:
                with open(path, "rb") as f:
                    img_bytes = f.read()
                resp = await client.post(
                    f"https://graph.facebook.com/v18.0/{page_id}/photos",
                    data={"access_token": access_token, "published": "false"},
                    files={"source": ("slide.png", img_bytes, "image/png")},
                )
                if resp.status_code == 200:
                    pid = resp.json().get("id")
                    if pid:
                        photo_ids.append(pid)

            if photo_ids:
                attached = [{"media_fbid": pid} for pid in photo_ids]
                resp = await client.post(
                    f"https://graph.facebook.com/v18.0/{page_id}/feed",
                    data={
                        "message": text,
                        "access_token": access_token,
                        "attached_media": str(attached).replace("'", '"'),
                    },
                )
                resp.raise_for_status()
                fb_post_id = resp.json().get("id", "")
                return PublishResult(
                    success=True,
                    platform_post_id=fb_post_id,
                    platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
                )

        # Text-only fallback
        params: dict = {"message": text, "access_token": access_token}
        if post.link_url:
            params["link"] = post.link_url
        resp = await client.post(f"https://graph.facebook.com/v18.0/{page_id}/feed", params=params)
        resp.raise_for_status()
        fb_post_id = resp.json().get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=fb_post_id,
            platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
        )


# ── Instagram ─────────────────────────────────────────────────────────────────

async def _publish_instagram(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
    media_paths: list[str],
) -> PublishResult:
    ig_user_id = account.account_id
    # Instagram requires publicly accessible image URLs — local paths can't be used directly.
    # Fall back to image_url from platform_specific if provided (e.g. set manually or via CDN).
    image_urls: list[str] = (post.platform_specific or {}).get("instagram", {}).get("image_urls", [])
    single_url: str | None = (post.platform_specific or {}).get("instagram", {}).get("image_url")
    if single_url and not image_urls:
        image_urls = [single_url]

    if not image_urls:
        return PublishResult(
            success=False,
            error="Instagram requires publicly accessible image URLs. Set platform_specific.instagram.image_urls or host images on a CDN.",
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        if len(image_urls) == 1:
            # Single image post
            container_resp = await client.post(
                f"https://graph.facebook.com/v18.0/{ig_user_id}/media",
                params={"image_url": image_urls[0], "caption": text[:2200], "access_token": access_token},
            )
            container_resp.raise_for_status()
            creation_id = container_resp.json().get("id")
        else:
            # Carousel post
            child_ids: list[str] = []
            for url in image_urls[:10]:
                cr = await client.post(
                    f"https://graph.facebook.com/v18.0/{ig_user_id}/media",
                    params={"image_url": url, "is_carousel_item": "true", "access_token": access_token},
                )
                if cr.status_code == 200:
                    cid = cr.json().get("id")
                    if cid:
                        child_ids.append(cid)

            if not child_ids:
                return PublishResult(success=False, error="No Instagram carousel items could be created")

            carousel_resp = await client.post(
                f"https://graph.facebook.com/v18.0/{ig_user_id}/media",
                params={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": text[:2200],
                    "access_token": access_token,
                },
            )
            carousel_resp.raise_for_status()
            creation_id = carousel_resp.json().get("id")

        publish_resp = await client.post(
            f"https://graph.facebook.com/v18.0/{ig_user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": access_token},
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json().get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=media_id,
            platform_url=f"https://www.instagram.com/p/{media_id}" if media_id else None,
        )
