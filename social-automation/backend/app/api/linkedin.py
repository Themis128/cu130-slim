"""LinkedIn-specific API endpoints: AI content generation and direct API helpers."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId, require_team_admin
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.analytics import PostAnalyticsSnapshot
from app.models.content import Post
from app.models.linkedin_intel import LinkedInAdIntel, LinkedInIntelWatch
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.linkedin_ai import (
    generate_linkedin_article,
    generate_linkedin_comment,
    generate_linkedin_hashtags,
    generate_linkedin_post,
    improve_linkedin_post,
    rank_best_time_windows,
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
    provider: str = "dmr"
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
    provider: str = "dmr"
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
    provider: str = "dmr"
    model: str | None = None


class GenerateHashtagsResponse(BaseModel):
    hashtags: list[str]


class BestTimeResponse(BaseModel):
    best_times: list[dict]
    source: str = "defaults"  # "analytics" when derived from published-post history
    posts_analyzed: int = 0


class ImprovePostRequest(BaseModel):
    content: str
    goal: str = "engagement"
    tone: str = "professional"
    provider: str = "dmr"
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
    provider: str = "dmr"
    model: str | None = None


class GenerateCommentResponse(BaseModel):
    comment: str


async def _team_for_user(db: AsyncSession, team_id: uuid.UUID) -> Team:
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=400, detail="No team found")
    return team


@router.post("/generate-post", response_model=GeneratePostResponse)
async def linkedin_generate_post(
    request: GeneratePostRequest, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Generate a LinkedIn-ready post with plain-English caption + hashtags."""
    team = await _team_for_user(db, team_id)
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
    request: GenerateArticleRequest, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Generate a long-form LinkedIn article in plain English."""
    team = await _team_for_user(db, team_id)
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
    request: GenerateHashtagsRequest, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Suggest LinkedIn hashtags for the supplied content."""
    team = await _team_for_user(db, team_id)
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
    team_id: TeamId, account_type: str = Query("organization"), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Return recommended posting windows for LinkedIn.

    Derives windows from the team's own published-post engagement history
    (Post.published_at bucketed by weekday/hour, weighted by each post's max
    engagement_rate). Falls back to professional-audience defaults when fewer
    than 10 posts with analytics exist.
    """
    team = await _team_for_user(db, team_id)

    # Which of the team's LinkedIn accounts match the requested type? If none
    # match exactly, fall back to all LinkedIn accounts for the team.
    base = select(SocialAccount.id).where(
        SocialAccount.team_id == team.id,
        SocialAccount.platform == "linkedin",
    )
    typed_ids = list((await db.execute(base.where(SocialAccount.account_type == account_type))).scalars().all())
    account_ids = typed_ids or list((await db.execute(base)).scalars().all())

    samples: list[tuple[datetime, float]] = []
    if account_ids:
        # Max engagement_rate per post — lifetime engagement correlates with
        # publish time; captured_at (sync cron time) must NOT be used here.
        stmt = (
            select(Post.published_at, func.max(PostAnalyticsSnapshot.engagement_rate))
            .join(PostAnalyticsSnapshot, PostAnalyticsSnapshot.post_id == Post.id)
            .where(
                PostAnalyticsSnapshot.team_id == team.id,
                PostAnalyticsSnapshot.platform == "linkedin",
                PostAnalyticsSnapshot.social_account_id.in_(account_ids),
                Post.published_at.is_not(None),
            )
            .group_by(Post.id, Post.published_at)
        )
        samples = [(published_at, rate) for published_at, rate in (await db.execute(stmt)).all() if published_at is not None]

    windows = rank_best_time_windows(samples) if samples else []
    if not windows:
        windows = await suggest_best_time_to_post(account_type=account_type)
    return BestTimeResponse(
        best_times=windows,
        source="analytics" if windows and "sample_size" in windows[0] else "defaults",
        posts_analyzed=len(samples),
    )


@router.post("/improve-post", response_model=ImprovePostResponse)
async def linkedin_improve_post(
    request: ImprovePostRequest, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Improve an existing LinkedIn post for a specific goal."""
    team = await _team_for_user(db, team_id)
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
    request: GenerateCommentRequest, team_id: TeamId, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Generate a plain-English LinkedIn comment or reply."""
    team = await _team_for_user(db, team_id)
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


# ── LinkedIn DM (messaging) auto-reply ──────────────────────────────────────


class LinkedInAutoReplyConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "You are a helpful assistant for {account_name}. Reply concisely and professionally."
    model: str = "@cf/meta/llama-3.1-8b-instruct"
    fallback_text: str = "Thanks for your message! I'll get back to you soon."
    max_tokens: int = 250
    cooldown_seconds: int = 300
    temperature: float = 0.7


@router.get("/{account_id}/dm/auto-reply")
async def get_linkedin_dm_auto_reply(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the LinkedIn DM auto-reply configuration for an account."""
    account = await _load_linkedin_account(db, account_id, current_user)
    meta = account.meta_data or {}
    config = meta.get("linkedin_auto_reply", {})
    return LinkedInAutoReplyConfig(
        enabled=config.get("enabled", False),
        system_prompt=config.get("system_prompt", LinkedInAutoReplyConfig().system_prompt),
        model=config.get("model", "@cf/meta/llama-3.1-8b-instruct"),
        fallback_text=config.get("fallback_text", LinkedInAutoReplyConfig().fallback_text),
        max_tokens=config.get("max_tokens", 250),
        cooldown_seconds=config.get("cooldown_seconds", 300),
        temperature=config.get("temperature", 0.7),
    )


@router.put("/{account_id}/dm/auto-reply")
async def update_linkedin_dm_auto_reply(
    account_id: uuid.UUID,
    body: LinkedInAutoReplyConfig,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the LinkedIn DM auto-reply configuration for an account."""
    account = await _load_linkedin_account(db, account_id, current_user)
    if body.enabled:
        from app.api.deps import check_plan_feature

        await check_plan_feature("dm_auto_reply", account.team_id, db)
    meta = account.meta_data or {}
    meta["linkedin_auto_reply"] = body.model_dump()
    account.meta_data = meta
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(account, "meta_data")
    await db.commit()
    return body


@router.get("/{account_id}/dm/conversations")
async def list_linkedin_dm_conversations(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List LinkedIn DM conversations via the browser sidecar."""
    await _load_linkedin_account(db, account_id, current_user)
    from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError

    sidecar = LinkedInSidecarClient()
    try:
        result = await sidecar.get_conversations()
        return result
    except LinkedInSidecarError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{account_id}/dm/threads/{thread_id}")
async def read_linkedin_dm_thread(
    account_id: uuid.UUID,
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read messages in a LinkedIn DM thread via the browser sidecar."""
    await _load_linkedin_account(db, account_id, current_user)
    from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError

    sidecar = LinkedInSidecarClient()
    try:
        result = await sidecar.get_thread_messages(thread_id)
        return result
    except LinkedInSidecarError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{account_id}/dm/threads/{thread_id}/send")
async def send_linkedin_dm(
    account_id: uuid.UUID,
    thread_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message in a LinkedIn DM thread via the browser sidecar."""
    await _load_linkedin_account(db, account_id, current_user)
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError

    sidecar = LinkedInSidecarClient()
    try:
        result = await sidecar.send_message(thread_id, text)
        return result
    except LinkedInSidecarError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


class AdsControlRequest(BaseModel):
    action: str  # "pause" | "resume" | "status"


@router.post("/ads-control")
async def linkedin_ads_control(
    body: AdsControlRequest,
    current_user: User = Depends(get_current_user),
):
    """Pause/resume/status for the tracked LinkedIn ad set via the CM sidecar.

    Enqueues the control task — the sidecar toggle is slow, so callers get the
    queued ack and the outcome is posted to Slack by the task.
    """
    from app.worker.tasks.linkedin_ads_control import linkedin_ads_control as task

    action = body.action.strip().lower()
    if action not in ("pause", "resume", "status"):
        raise HTTPException(status_code=400, detail="action must be pause|resume|status")
    task.delay(action=action, response_url=None, user=current_user.email or "admin")
    return {"ok": True, "queued": action}


# ── Ad Library intelligence ──────────────────────────────────────────────────


def _intel_ad_out(row: LinkedInAdIntel) -> dict:
    return {
        "id": str(row.id),
        "ad_url": row.ad_url,
        "advertiser": row.advertiser,
        "ad_payer": row.ad_payer,
        "advertiser_url": row.advertiser_url,
        "is_restricted": row.is_restricted,
        "impressions_from": row.impressions_from,
        "impressions_to": row.impressions_to,
        "first_impression_at": row.first_impression_at,
        "latest_impression_at": row.latest_impression_at,
        "countries": row.countries,
        "targeting": row.targeting,
        "status": row.status,
        "last_keyword": row.last_keyword,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "miss_count": row.miss_count,
    }


@router.get("/intel/ads")
async def linkedin_intel_ads(
    status: str | None = Query(default=None, pattern="^(active|ended|all)$"),
    advertiser: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List stored Ad Library observations (competitive intel)."""
    stmt = select(LinkedInAdIntel).order_by(LinkedInAdIntel.last_seen_at.desc())
    if status and status != "all":
        stmt = stmt.where(LinkedInAdIntel.status == status)
    if advertiser:
        stmt = stmt.where(LinkedInAdIntel.advertiser.ilike(f"%{advertiser}%"))
    if keyword:
        stmt = stmt.where(LinkedInAdIntel.last_keyword.ilike(f"%{keyword}%"))
    rows = (await db.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return {"ads": [_intel_ad_out(r) for r in rows], "count": len(rows)}


@router.get("/intel/summary")
async def linkedin_intel_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate intel stats: counts by status, top advertisers, recent activity."""
    total = (await db.execute(select(func.count(LinkedInAdIntel.id)))).scalar_one()
    active = (
        await db.execute(
            select(func.count(LinkedInAdIntel.id)).where(LinkedInAdIntel.status == "active")
        )
    ).scalar_one()
    ended = (
        await db.execute(
            select(func.count(LinkedInAdIntel.id)).where(LinkedInAdIntel.status == "ended")
        )
    ).scalar_one()
    top_advertisers = (
        await db.execute(
            select(LinkedInAdIntel.advertiser, func.count(LinkedInAdIntel.id).label("n"))
            .group_by(LinkedInAdIntel.advertiser)
            .order_by(func.count(LinkedInAdIntel.id).desc())
            .limit(10)
        )
    ).all()
    newest = (
        await db.execute(
            select(LinkedInAdIntel).order_by(LinkedInAdIntel.first_seen_at.desc()).limit(10)
        )
    ).scalars().all()
    return {
        "total": total,
        "active": active,
        "ended": ended,
        "top_advertisers": [{"advertiser": a, "ads": n} for a, n in top_advertisers],
        "newest": [_intel_ad_out(r) for r in newest],
    }


@router.get("/intel/watchlist")
async def linkedin_intel_watchlist(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(LinkedInIntelWatch).order_by(LinkedInIntelWatch.created_at))).scalars().all()
    return {
        "watches": [
            {
                "id": str(w.id),
                "kind": w.kind,
                "value": w.value,
                "country": w.country,
                "enabled": w.enabled,
                "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None,
            }
            for w in rows
        ]
    }


class IntelWatchRequest(BaseModel):
    kind: str  # keyword | advertiser
    value: str
    country: str = "GR"
    enabled: bool = True


@router.post("/intel/watchlist")
async def linkedin_intel_watch_add(
    body: IntelWatchRequest,
    current_user: User = Depends(require_team_admin),
    db: AsyncSession = Depends(get_db),
):
    kind = body.kind.strip().lower()
    value = body.value.strip()
    if kind not in ("keyword", "advertiser") or not value:
        raise HTTPException(status_code=400, detail="kind must be keyword|advertiser and value required")
    dup = (
        await db.execute(
            select(LinkedInIntelWatch).where(
                LinkedInIntelWatch.kind == kind,
                LinkedInIntelWatch.value == value,
                LinkedInIntelWatch.country == body.country.strip().upper(),
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="watch already exists")
    w = LinkedInIntelWatch(
        kind=kind, value=value[:300], country=body.country.strip().upper()[:10], enabled=body.enabled
    )
    db.add(w)
    await db.commit()
    return {"id": str(w.id), "kind": w.kind, "value": w.value, "country": w.country}


@router.delete("/intel/watchlist/{watch_id}")
async def linkedin_intel_watch_delete(
    watch_id: uuid.UUID,
    current_user: User = Depends(require_team_admin),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(LinkedInIntelWatch, watch_id)
    if not w:
        raise HTTPException(status_code=404, detail="watch not found")
    await db.delete(w)
    await db.commit()
    return {"deleted": str(watch_id)}


@router.post("/intel/run")
async def linkedin_intel_run(
    current_user: User = Depends(require_team_admin),
):
    """Enqueue an ad-hoc watch run (same task as the weekly beat)."""
    from app.worker.tasks.linkedin_intel import run_linkedin_ad_library_watch

    run_linkedin_ad_library_watch.delay()
    return {"ok": True, "queued": "linkedin-ad-library-watch"}
