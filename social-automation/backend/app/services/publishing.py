import dataclasses
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.content import Post
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
        return await fn(access_token, text, account, post)
    except httpx.HTTPStatusError as exc:
        return PublishResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return PublishResult(success=False, error=str(exc))


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


async def _publish_twitter(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
) -> PublishResult:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.twitter.com/2/tweets",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"text": text[:280]},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        tweet_id = data.get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=tweet_id,
            platform_url=f"https://twitter.com/i/web/status/{tweet_id}" if tweet_id else None,
        )


async def _publish_linkedin(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
) -> PublishResult:
    author_urn = f"urn:li:person:{account.account_id}"
    payload: dict = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text[:3000]},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json=payload,
        )
        resp.raise_for_status()
        post_id = resp.headers.get("x-restli-id", "")
        return PublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
        )


async def _publish_facebook(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
) -> PublishResult:
    page_id = account.account_id
    params: dict = {"message": text, "access_token": access_token}
    if post.link_url:
        params["link"] = post.link_url

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://graph.facebook.com/v18.0/{page_id}/feed",
            params=params,
        )
        resp.raise_for_status()
        fb_post_id = resp.json().get("id", "")
        return PublishResult(
            success=True,
            platform_post_id=fb_post_id,
            platform_url=f"https://www.facebook.com/{fb_post_id}" if fb_post_id else None,
        )


async def _publish_instagram(
    access_token: str,
    text: str,
    account: SocialAccount,
    post: Post,
) -> PublishResult:
    ig_user_id = account.account_id

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: create media container
        container_params: dict = {"caption": text[:2200], "access_token": access_token}
        if post.media_ids:
            # If there are media assets, use image_url from metadata (populated by media upload flow)
            image_url = (post.platform_specific or {}).get("instagram", {}).get("image_url")
            if image_url:
                container_params["image_url"] = image_url
                container_params["media_type"] = "IMAGE"
        else:
            # Text-only posts aren't supported on Instagram; use a placeholder
            return PublishResult(success=False, error="Instagram requires media; text-only posts are not supported")

        container_resp = await client.post(
            f"https://graph.facebook.com/v18.0/{ig_user_id}/media",
            params=container_params,
        )
        container_resp.raise_for_status()
        creation_id = container_resp.json().get("id")

        # Step 2: publish the container
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
