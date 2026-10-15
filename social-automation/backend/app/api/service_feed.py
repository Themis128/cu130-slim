"""Read-only JSON feeds for the IntraVox intranet (cloud.cloudless.gr).

IntraVox feed widgets consume these through a "custom" REST connection:
``baseUrl + customEndpoint`` with ``authMethod=apikey`` (header
``X-Service-Token``) and ``responseMapping`` pointing at ``items[]`` —
each item maps ``title/url/excerpt/date/author/image``.

Auth: ``X-Service-Token`` must match ``INTRANET_FEED_TOKEN``. Feeds are
scoped to a single team: ``INTRANET_FEED_TEAM_ID`` when set, otherwise the
team with the most active connected accounts (same populated-team rule the
Slack digest uses — skips empty CI fixtures).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.content import MediaAsset, Post, PostTarget
from app.models.queue import PublishQueue
from app.models.social_account import SocialAccount
from app.models.user import Team

router = APIRouter(tags=["service-feed"])

_FEED_CACHE_HEADERS = {"Cache-Control": "private, max-age=300"}


def _require_feed_token(request: Request) -> None:
    settings = get_settings()
    expected = (settings.INTRANET_FEED_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INTRANET_FEED_TOKEN is not configured",
        )
    provided = (request.headers.get("X-Service-Token") or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


class FeedItem(BaseModel):
    title: str
    url: str = ""
    excerpt: str = ""
    date: str = ""
    author: str | None = None
    image: str | None = None


class FeedResponse(BaseModel):
    source: str
    generated_at: str
    items: list[FeedItem] = Field(default_factory=list)


async def _feed_team(db: AsyncSession) -> Team | None:
    settings = get_settings()
    pinned = (settings.INTRANET_FEED_TEAM_ID or "").strip()
    if pinned:
        try:
            team_id = uuid.UUID(pinned)
        except ValueError:
            raise HTTPException(
                status_code=500,
                detail="INTRANET_FEED_TEAM_ID is not a valid UUID",
            ) from None
        return (
            await db.execute(select(Team).where(Team.id == team_id))
        ).scalar_one_or_none()

    # Populated team wins — empty CI/test teams have no connected accounts.
    rows = (
        await db.execute(
            select(SocialAccount.team_id, func.count(SocialAccount.id))
            .where(SocialAccount.status == "active")
            .group_by(SocialAccount.team_id)
            .order_by(func.count(SocialAccount.id).desc())
            .limit(1)
        )
    ).all()
    if not rows:
        return None
    return (
        await db.execute(select(Team).where(Team.id == rows[0][0]))
    ).scalar_one_or_none()


def _snippet(text: str | None, limit: int = 120) -> str:
    return " ".join((text or "").split())[:limit]


async def _media_image_map(
    db: AsyncSession, posts: list[Post]
) -> dict[uuid.UUID, str]:
    """First media asset's public_url per post — single batch query."""
    first_ids = [p.media_ids[0] for p in posts if p.media_ids]
    if not first_ids:
        return {}
    rows = (
        await db.execute(
            select(MediaAsset.id, MediaAsset.public_url).where(
                MediaAsset.id.in_(first_ids),
                MediaAsset.public_url.is_not(None),
            )
        )
    ).all()
    url_by_id = {row[0]: row[1] for row in rows}
    return {p.id: url_by_id[p.media_ids[0]] for p in posts if p.media_ids and p.media_ids[0] in url_by_id}


@router.get("", response_model=dict[str, Any])
async def feed_index(request: Request) -> dict[str, Any]:
    """Discovery endpoint — lists the feeds this token can read."""
    _require_feed_token(request)
    return {
        "feeds": [
            {"path": "/service-feed/activity", "kind": "recent publish outcomes"},
            {"path": "/service-feed/digest", "kind": "daily digest items"},
            {"path": "/service-feed/status", "kind": "queue + account health"},
            {"path": "/service-feed/leads", "kind": "recent inbound leads"},
        ],
        "mapping_hint": {
            "items": "items",
            "title": "title",
            "url": "url",
            "excerpt": "excerpt",
            "date": "date",
            "author": "author",
            "image": "image",
        },
    }


@router.get("/activity", response_model=FeedResponse)
async def feed_activity(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Recent publish outcomes (published/failed targets), newest first."""
    _require_feed_token(request)
    team = await _feed_team(db)
    if team is None:
        return FeedResponse(
            source="SocialAuto activity",
            generated_at=datetime.now(UTC).isoformat(),
            items=[],
        )

    rows = (
        await db.execute(
            select(PostTarget, Post, SocialAccount)
            .join(Post, PostTarget.post_id == Post.id)
            .join(SocialAccount, PostTarget.social_account_id == SocialAccount.id)
            .where(Post.team_id == team.id, PostTarget.status != "pending")
            .order_by(Post.updated_at.desc())
            .limit(limit)
        )
    ).all()

    images = await _media_image_map(db, [p for _, p, _ in rows])
    items: list[FeedItem] = []
    for target, post, account in rows:
        account_label = account.display_name or account.username or account.platform
        status = target.status or "unknown"
        title = _snippet(post.content_text, 80) or f"{account.platform} post"
        excerpt_bits = [f"{account.platform} · @{account_label} — {status}"]
        if status == "failed" and target.error_message:
            excerpt_bits.append(_snippet(target.error_message, 140))
        when = target.published_at or post.updated_at
        items.append(
            FeedItem(
                title=title,
                url=target.platform_url or "",
                excerpt=" · ".join(excerpt_bits),
                date=when.isoformat() if when else "",
                author=account_label,
                image=images.get(post.id),
            )
        )
    return FeedResponse(
        source="SocialAuto activity",
        generated_at=datetime.now(UTC).isoformat(),
        items=items,
    )


@router.get("/digest", response_model=FeedResponse)
async def feed_digest(
    request: Request,
    days: int = Query(1, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Today's digest reshaped as feed items (glance, top posts, issues)."""
    _require_feed_token(request)
    team = await _feed_team(db)
    if team is None:
        return FeedResponse(
            source="SocialAuto daily digest",
            generated_at=datetime.now(UTC).isoformat(),
            items=[],
        )

    from app.services.slack_digest import build_daily_digest

    report = await build_daily_digest(db, team=team, days=days)
    o = report.overview
    stamp = report.generated_at.isoformat()
    items = [
        FeedItem(
            title=(
                f"Daily digest — {o.get('published_posts', 0)} published · "
                f"{o.get('failed_posts', 0)} need attention"
            ),
            excerpt=(
                f"Posts {o.get('total_posts', 0)} total · "
                f"{o.get('scheduled_posts', 0)} scheduled · "
                f"{o.get('draft_posts', 0)} drafts · "
                f"accounts {o.get('connected_accounts', 0)} · "
                f"24h impressions {report.impressions_24h} · "
                f"engagement {report.engagement_24h}"
            ),
            date=stamp,
            author=report.team_name,
        )
    ]
    for p in report.top_posts[:5]:
        items.append(
            FeedItem(
                title=_snippet(p.get("snippet"), 80) or f"post {p.get('post_id')}",
                url=p.get("url") or "",
                excerpt=(
                    f"top post · engagement {p.get('engagement', 0)} · "
                    f"impressions {p.get('impressions', 0)}"
                ),
                date=stamp,
                author=report.team_name,
            )
        )
    for issue in report.issues:
        items.append(
            FeedItem(
                title=issue.title,
                excerpt=issue.detail or issue.severity,
                date=stamp,
                author=issue.severity,
            )
        )
    return FeedResponse(
        source="SocialAuto daily digest",
        generated_at=stamp,
        items=items,
    )


@router.get("/status", response_model=FeedResponse)
async def feed_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Operational health items — queue depth + accounts needing attention."""
    _require_feed_token(request)
    team = await _feed_team(db)
    stamp = datetime.now(UTC).isoformat()
    if team is None:
        return FeedResponse(
            source="SocialAuto status", generated_at=stamp, items=[]
        )

    queue_counts = (
        await db.execute(
            select(PublishQueue.status, func.count())
            .join(PublishQueue.post)
            .where(Post.team_id == team.id)
            .group_by(PublishQueue.status)
        )
    ).all()
    queue = {
        str(s.value if hasattr(s, "value") else s): c for s, c in queue_counts
    }

    attention = (
        await db.execute(
            select(SocialAccount)
            .where(
                SocialAccount.team_id == team.id,
                SocialAccount.status != "active",
            )
            .order_by(SocialAccount.platform)
        )
    ).scalars().all()

    items = [
        FeedItem(
            title=(
                f"Publish queue — {queue.get('pending', 0)} pending · "
                f"{queue.get('processing', 0)} processing · "
                f"{queue.get('failed', 0)} failed"
            ),
            excerpt=" · ".join(f"{k} {v}" for k, v in sorted(queue.items()))
            or "queue empty",
            date=stamp,
            author="publishing",
        )
    ]
    for acc in attention:
        label = acc.display_name or acc.username or acc.platform
        items.append(
            FeedItem(
                title=f"{acc.platform} · @{label} — {acc.status}",
                excerpt="account needs reconnection or review",
                date=stamp,
                author=label,
            )
        )
    if not attention:
        items.append(
            FeedItem(
                title="All connected accounts healthy",
                excerpt="no expired or revoked account tokens",
                date=stamp,
                author="accounts",
            )
        )
    return FeedResponse(
        source="SocialAuto status", generated_at=stamp, items=items
    )


@router.get("/leads", response_model=FeedResponse)
async def feed_leads(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    """Recent inbound leads — names and sources, never raw emails."""
    _require_feed_token(request)
    team = await _feed_team(db)
    stamp = datetime.now(UTC).isoformat()
    if team is None:
        return FeedResponse(
            source="SocialAuto leads", generated_at=stamp, items=[]
        )

    from app.models.lead import Lead

    leads = (
        await db.execute(
            select(Lead)
            .where(Lead.team_id == team.id)
            .order_by(Lead.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    items = [
        FeedItem(
            title=f"{lead.name} — {lead.source}",
            excerpt=" · ".join(
                part
                for part in (
                    f"interest: {lead.interest}" if lead.interest else "",
                    f"company: {lead.company_size}" if lead.company_size else "",
                    _snippet(lead.notes, 100),
                )
                if part
            ),
            date=lead.created_at.isoformat(),
            author=str(lead.source),
        )
        for lead in leads
    ]
    return FeedResponse(
        source="SocialAuto leads", generated_at=stamp, items=items
    )
