"""LinkedIn-specific API endpoints: AI content generation and direct API helpers."""

from __future__ import annotations

import dataclasses
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.linkedin_ai import (
    generate_linkedin_article,
    generate_linkedin_comment,
    generate_linkedin_hashtags,
    generate_linkedin_post,
    improve_linkedin_post,
    suggest_best_time_to_post,
)
from app.services.linkedin_api import LinkedInAPIClient, LinkedInAPIError

router = APIRouter()


# ── AI content generation ────────────────────────────────────────────────────


class GeneratePostRequest(BaseModel):
    topic: str
    tone: str = "professional"
    length: str = "medium"
    include_hashtags: bool = True
    include_site_link: bool = True
    site: str = "www.cloudless.gr"
    provider: str = "cloudflare"
    model: str | None = None


class GeneratePostResponse(BaseModel):
    caption: str
    content: str
    hashtags: list[str]
    topic: str
    tone: str
    length: str


class GenerateArticleRequest(BaseModel):
    topic: str
    tone: str = "professional"
    sections: int = 5
    include_takeaways: bool = True
    include_cta: bool = True
    provider: str = "cloudflare"
    model: str | None = None


class ArticleSection(BaseModel):
    heading: str
    body: str


class GenerateArticleResponse(BaseModel):
    title: str
    subtitle: str
    sections: list[ArticleSection]
    body: str
    takeaways: list[str]
    cta: str
    topic: str
    tone: str


class GenerateHashtagsRequest(BaseModel):
    content: str
    count: int = 5
    provider: str = "cloudflare"
    model: str | None = None


class GenerateHashtagsResponse(BaseModel):
    hashtags: list[str]


class BestTimeResponse(BaseModel):
    best_times: list[dict]


class ImprovePostRequest(BaseModel):
    content: str
    goal: str = "engagement"
    tone: str = "professional"
    provider: str = "cloudflare"
    model: str | None = None


class ImprovePostResponse(BaseModel):
    improved_content: str
    changes: list[str]
    hashtags: list[str]


class GenerateCommentRequest(BaseModel):
    post_text: str
    reply_context: str = ""
    tone: str = "professional"
    length: str = "short"
    provider: str = "cloudflare"
    model: str | None = None


class GenerateCommentResponse(BaseModel):
    comment: str


async def _team_for_user(db: AsyncSession, user: User) -> Team:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user.id))
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")
    return team


@router.post("/generate-post", response_model=GeneratePostResponse)
async def linkedin_generate_post(
    request: GeneratePostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a LinkedIn-ready post with plain-English caption + hashtags."""
    team = await _team_for_user(db, current_user)
    result = await generate_linkedin_post(
        request.topic,
        tone=request.tone,
        length=request.length,
        include_hashtags=request.include_hashtags,
        include_site_link=request.include_site_link,
        site=request.site,
        provider=request.provider,
        model=request.model,
        db=db,
        team_id=team.id,
    )
    return GeneratePostResponse(**result)


@router.post("/generate-article", response_model=GenerateArticleResponse)
async def linkedin_generate_article(
    request: GenerateArticleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a long-form LinkedIn article in plain English."""
    team = await _team_for_user(db, current_user)
    result = await generate_linkedin_article(
        request.topic,
        tone=request.tone,
        sections=request.sections,
        include_takeaways=request.include_takeaways,
        include_cta=request.include_cta,
        provider=request.provider,
        model=request.model,
        db=db,
        team_id=team.id,
    )
    return GenerateArticleResponse(**result)


@router.post("/generate-hashtags", response_model=GenerateHashtagsResponse)
async def linkedin_generate_hashtags(
    request: GenerateHashtagsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Suggest LinkedIn hashtags for the supplied content."""
    team = await _team_for_user(db, current_user)
    hashtags = await generate_linkedin_hashtags(
        request.content,
        count=request.count,
        provider=request.provider,
        model=request.model,
        db=db,
        team_id=team.id,
    )
    return GenerateHashtagsResponse(hashtags=hashtags)


@router.get("/best-time", response_model=BestTimeResponse)
async def linkedin_best_time(
    account_type: str = Query("organization"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return recommended posting windows for LinkedIn."""
    # Team lookup keeps the route consistent with others; no DB write needed.
    await _team_for_user(db, current_user)
    return BestTimeResponse(best_times=await suggest_best_time_to_post(account_type=account_type))


@router.post("/improve-post", response_model=ImprovePostResponse)
async def linkedin_improve_post(
    request: ImprovePostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Improve an existing LinkedIn post for a specific goal."""
    team = await _team_for_user(db, current_user)
    result = await improve_linkedin_post(
        request.content,
        goal=request.goal,
        tone=request.tone,
        provider=request.provider,
        model=request.model,
        db=db,
        team_id=team.id,
    )
    return ImprovePostResponse(**result)


@router.post("/generate-comment", response_model=GenerateCommentResponse)
async def linkedin_generate_comment(
    request: GenerateCommentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a plain-English LinkedIn comment or reply."""
    team = await _team_for_user(db, current_user)
    comment = await generate_linkedin_comment(
        request.post_text,
        reply_context=request.reply_context,
        tone=request.tone,
        length=request.length,
        provider=request.provider,
        model=request.model,
        db=db,
        team_id=team.id,
    )
    return GenerateCommentResponse(comment=comment)


# ── Direct LinkedIn API helpers ──────────────────────────────────────────────


class PublishPostRequest(BaseModel):
    account_id: uuid.UUID
    commentary: str
    link_url: str | None = None
    link_title: str | None = None
    link_description: str | None = None
    visibility: str = "PUBLIC"


class PublishPostResponse(BaseModel):
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


class CommentRequest(BaseModel):
    account_id: uuid.UUID
    post_urn: str
    text: str


class CommentResponse(BaseModel):
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


async def _load_linkedin_account(
    db: AsyncSession,
    account_id: uuid.UUID,
    user: User,
) -> SocialAccount:
    """Load a LinkedIn account and verify the user can access it."""
    result = await db.execute(
        select(SocialAccount)
        .join(Team, SocialAccount.team_id == Team.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.platform == "linkedin",
            TeamMember.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="LinkedIn account not found")
    if account.status != "active":
        raise HTTPException(status_code=400, detail="LinkedIn account is not active")
    return account


def _author_urn_for_account(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    if meta.get("author_urn"):
        return str(meta["author_urn"])
    account_type = (meta.get("account_type") or "person").lower()
    if account_type in ("organization", "company", "page"):
        return f"urn:li:organization:{account.account_id}"
    return f"urn:li:person:{account.account_id}"


def _org_urn_for_account(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
        return str(meta["author_urn"])
    return f"urn:li:organization:{account.account_id}"


@router.get("/accounts/{account_id}/validate")
async def validate_linkedin_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate a stored LinkedIn token and list accessible Company Pages."""
    account = await _load_linkedin_account(db, account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    try:
        profile = await client.validate_token()
    except LinkedInAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text[:400]) from exc
    organizations = await client.get_member_organizations()

    return {
        "valid": True,
        "profile": profile,
        "organizations": [
            {
                "urn": o.urn,
                "id": o.id,
                "name": o.name,
                "vanity_name": o.vanity_name,
                "role": o.role,
            }
            for o in organizations
        ],
        "account_id": str(account.id),
    }


@router.get("/accounts/{account_id}/followers")
async def linkedin_follower_count(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the follower count for a LinkedIn Company Page."""
    account = await _load_linkedin_account(db, account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    org_urn = _org_urn_for_account(account)
    count = await client.get_follower_count(org_urn)
    return {"account_id": str(account.id), "org_urn": org_urn, "followers": count}


@router.get("/analytics/post/{post_urn}")
async def linkedin_post_analytics(
    post_urn: str,
    account_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch lifetime statistics for a single LinkedIn post."""
    account = await _load_linkedin_account(db, account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    org_urn = _org_urn_for_account(account)
    try:
        stats = await client.get_post_analytics(post_urn, org_urn)
    except LinkedInAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text[:400]) from exc
    if not stats:
        raise HTTPException(status_code=404, detail="No analytics found for post")
    return {"account_id": str(account.id), "post_urn": post_urn, "stats": stats}


@router.get("/analytics/organization")
async def linkedin_organization_analytics(
    account_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch aggregated lifetime statistics for the whole Company Page."""
    account = await _load_linkedin_account(db, account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    org_urn = _org_urn_for_account(account)
    try:
        stats = await client.get_organization_lifetime_stats(org_urn)
    except LinkedInAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.response_text[:400]) from exc
    if not stats:
        raise HTTPException(status_code=404, detail="No organization analytics found")
    return {"account_id": str(account.id), "org_urn": org_urn, "stats": stats}


@router.post("/publish", response_model=PublishPostResponse)
async def linkedin_publish_post(
    request: PublishPostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a text or link-preview post directly to LinkedIn.

    For carousel / multi-image posts, use ``POST /ai/run-carousel-and-publish``.
    """
    account = await _load_linkedin_account(db, request.account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    author_urn = _author_urn_for_account(account)
    result = await client.create_post(
        author_urn=author_urn,
        commentary=request.commentary,
        visibility=request.visibility,
        link_url=request.link_url,
        link_title=request.link_title,
        link_description=request.link_description,
    )
    return PublishPostResponse(**dataclasses.asdict(result))  # type: ignore[arg-type]


@router.post("/comment", response_model=CommentResponse)
async def linkedin_post_comment(
    request: CommentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a comment as the selected LinkedIn account."""
    account = await _load_linkedin_account(db, request.account_id, current_user)
    token = decrypt_token(account.access_token_enc)

    client = LinkedInAPIClient(access_token=token)
    author_urn = _author_urn_for_account(account)
    result = await client.create_comment(
        post_urn=request.post_urn,
        text=request.text,
        creator_urn=author_urn,
    )
    return CommentResponse(**dataclasses.asdict(result))  # type: ignore[arg-type]


# ── Public helper for scripts / n8n ──────────────────────────────────────────


@router.get("/company-page-url")
async def linkedin_company_page_url(
    account_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the public LinkedIn Company Page URL for the connected account."""
    account = await _load_linkedin_account(db, account_id, current_user)
    meta = account.meta_data or {}
    vanity = meta.get("vanity_name") or account.username
    if vanity:
        return {"url": f"https://www.linkedin.com/company/{vanity}", "vanity_name": vanity}
    return {"url": None, "vanity_name": None, "account_id": account.account_id}
