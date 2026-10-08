"""Free Meta organic-growth workflows.

This router deliberately covers only owned-account publishing and scheduling.
Meta does not expose a supported Page-follower invitation API, and paid
promotion must never be silently substituted for an organic action.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId, check_quota
from app.db.session import get_db
from app.models.content import MediaAsset, Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import User

router = APIRouter()

META_PLATFORMS = frozenset({"facebook", "instagram", "threads"})
PAID_METADATA_KEYS = frozenset({"ad", "ads", "boost", "promote", "budget", "spend"})


class MetaGrowthReadiness(BaseModel):
    team_id: uuid.UUID
    accounts: list[dict[str, Any]]
    free_actions: list[str]
    unavailable_actions: list[dict[str, str]]


class OrganicCampaignRequest(BaseModel):
    content_text: str
    media_ids: list[uuid.UUID] = Field(default_factory=list)
    link_url: str | None = None
    target_account_ids: list[uuid.UUID] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    hashtags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_text")
    @classmethod
    def _content_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content_text is required")
        return value

    @model_validator(mode="after")
    def _safe_organic_request(self) -> "OrganicCampaignRequest":
        if not self.media_ids:
            raise ValueError(
                "At least one media asset is required for Meta organic promotion"
            )
        if PAID_METADATA_KEYS.intersection(
            str(key).lower() for key in self.metadata
        ):
            raise ValueError("Paid promotion fields are not supported by this endpoint")
        return self


class OrganicCampaignResponse(BaseModel):
    post_id: uuid.UUID
    status: PostStatus
    scheduled_at: datetime
    targets: list[dict[str, str]]
    free_actions: list[str]
    unavailable_actions: list[dict[str, str]]


@router.get("/readiness", response_model=MetaGrowthReadiness)
async def get_meta_growth_readiness(
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MetaGrowthReadiness:
    """Report what SocialAuto can do for Meta without paid promotion."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform.in_(META_PLATFORMS),
        ).order_by(SocialAccount.platform, SocialAccount.created_at.desc())
    )
    accounts = result.scalars().all()
    return MetaGrowthReadiness(
        team_id=team_id,
        accounts=[
            {
                "id": str(account.id),
                "platform": account.platform,
                "username": account.username,
                "display_name": account.display_name,
                "status": account.status,
                "account_type": account.account_type,
                "is_business": account.is_business,
            }
            for account in accounts
        ],
        free_actions=[
            "schedule_valid_media_posts",
            "publish_valid_media_posts",
            "cross_publish_to_connected_meta_accounts",
            "publish_instagram_stories",
            "publish_threads_posts",
        ],
        unavailable_actions=[
            {
                "action": "facebook_page_invitations",
                "reason": "Meta does not expose a supported Page-follower invitation API",
            },
            {
                "action": "paid_boosts_and_ads",
                "reason": "Paid promotion requires explicit campaign and spend authorization",
            },
        ],
    )


@router.post(
    "/organic-campaign",
    response_model=OrganicCampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organic_campaign(
    request: OrganicCampaignRequest,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganicCampaignResponse:
    """Create a queue-compatible, no-spend campaign across Meta accounts.

    The publishing worker performs the actual platform dispatch. Requiring
    media here preserves the product-wide rule that no post is published with
    missing or mismatched media.
    """
    await check_quota("posts_per_month", team_id, db)
    account_query = select(SocialAccount).where(
        SocialAccount.team_id == team_id,
        SocialAccount.platform.in_(META_PLATFORMS),
        SocialAccount.status == "active",
    )
    if request.target_account_ids:
        account_query = account_query.where(
            SocialAccount.id.in_(request.target_account_ids)
        )
    result = await db.execute(account_query.order_by(SocialAccount.platform))
    accounts = result.scalars().all()
    if not accounts:
        raise HTTPException(
            status_code=409,
            detail="Connect an active Facebook, Instagram, or Threads account first",
        )

    requested_ids = set(request.target_account_ids)
    if requested_ids and {account.id for account in accounts} != requested_ids:
        raise HTTPException(
            status_code=404,
            detail="One or more requested Meta accounts are unavailable to this team",
        )

    media_result = await db.execute(
        select(MediaAsset.id).where(
            MediaAsset.team_id == team_id,
            MediaAsset.id.in_(request.media_ids),
            MediaAsset.is_archived.is_(False),
        )
    )
    owned_media_ids = {row[0] for row in media_result.all()}
    if owned_media_ids != set(request.media_ids):
        raise HTTPException(
            status_code=404,
            detail="One or more media assets are unavailable to this team",
        )

    when = request.scheduled_at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    post = Post(
        team_id=team_id,
        user_id=current_user.id,
        status=PostStatus.SCHEDULED,
        content_text=request.content_text,
        media_ids=request.media_ids,
        hashtags=request.hashtags,
        link_url=request.link_url,
        scheduled_at=when,
        meta_data={**request.metadata, "organic_meta_campaign": True},
    )
    db.add(post)
    await db.flush()
    for account in accounts:
        db.add(PostTarget(post_id=post.id, social_account_id=account.id))
    await db.commit()
    await db.refresh(post)

    return OrganicCampaignResponse(
        post_id=post.id,
        status=post.status,
        scheduled_at=when,
        targets=[
            {
                "id": str(account.id),
                "platform": account.platform,
                "username": account.username or account.display_name or "",
            }
            for account in accounts
        ],
        free_actions=["queued_for_owned_account_publishing"],
        unavailable_actions=[
            {
                "action": "facebook_page_invitations",
                "reason": "Meta does not expose a supported Page-follower invitation API",
            },
            {
                "action": "paid_boost_or_ad",
                "reason": "Paid promotion is intentionally not part of this workflow",
            },
        ],
    )
