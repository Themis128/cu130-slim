"""Growth-initiative tracking — records effort events and measures their
follower impact on a social account.

Initiatives like LinkedIn's monthly Page-invite credits (invite up to 100
connections to follow a Page) are off-platform actions: LinkedIn does not
expose invitation stats via API, so the sends are recorded here as
``analytics_events`` rows (``post_id=NULL`` → they export to the datalake as
``account-events``) and the impact is measured by diffing the account's
``follower_snapshots`` before vs. after.

Usage:
    await record_initiative_event(db, team_id, "linkedin_page_invite",
        platform="linkedin", account_id=page.id, units=40,
        note="batch 1 — warm connections")
    summary = await initiative_summary(db, team_id)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, FollowerSnapshot

logger = logging.getLogger(__name__)

INITIATIVE_TYPES = {
    "linkedin_page_invite": "LinkedIn Page invitation credits",
    "growth_initiative": "Custom growth initiative",
}


async def record_initiative_event(
    db: AsyncSession,
    team_id: UUID,
    event_type: str,
    *,
    platform: str,
    account_id: UUID | None = None,
    units: int = 1,
    note: str | None = None,
) -> AnalyticsEvent:
    """Record one initiative action (e.g. 40 page invites sent)."""
    event = AnalyticsEvent(
        team_id=team_id,
        social_account_id=account_id,
        event_type=event_type,
        platform=platform,
        occurred_at=datetime.now(UTC),
        meta_data={"units": units, **({"note": note} if note else {})},
    )
    db.add(event)
    await db.commit()
    logger.info("Initiative event %s recorded: %s units on %s", event_type, units, platform)
    return event


async def _followers_at_or_before(
    db: AsyncSession, account_id: UUID, when: datetime
) -> int | None:
    row = (
        await db.execute(
            select(FollowerSnapshot.followers)
            .where(
                FollowerSnapshot.social_account_id == account_id,
                FollowerSnapshot.captured_at <= when,
            )
            .order_by(FollowerSnapshot.captured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def _followers_latest(db: AsyncSession, account_id: UUID) -> int | None:
    row = (
        await db.execute(
            select(FollowerSnapshot.followers)
            .where(FollowerSnapshot.social_account_id == account_id)
            .order_by(FollowerSnapshot.captured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def initiative_summary(
    db: AsyncSession, team_id: UUID, *, days: int = 30
) -> list[dict[str, Any]]:
    """Per-initiative rollup: units sent, events, follower start→now delta.

    Follower baseline is the snapshot nearest (at or before) the first
    initiative event; "now" is the latest snapshot. If no account is linked
    to the events, follower fields are null.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                AnalyticsEvent.event_type,
                AnalyticsEvent.platform,
                AnalyticsEvent.social_account_id,
                func.count(AnalyticsEvent.id).label("events"),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            cast(
                                AnalyticsEvent.meta_data["units"].astext,
                                Integer,
                            ),
                            1,
                        )
                    ),
                    0,
                ).label("units"),
                func.min(AnalyticsEvent.occurred_at).label("first_at"),
                func.max(AnalyticsEvent.occurred_at).label("last_at"),
            )
            .where(
                AnalyticsEvent.team_id == team_id,
                AnalyticsEvent.post_id.is_(None),
                AnalyticsEvent.event_type.in_(list(INITIATIVE_TYPES)),
                AnalyticsEvent.occurred_at >= since,
            )
            .group_by(
                AnalyticsEvent.event_type,
                AnalyticsEvent.platform,
                AnalyticsEvent.social_account_id,
            )
        )
    ).all()

    out: list[dict[str, Any]] = []
    for r in rows:
        followers_start = followers_now = delta = conv = None
        if r.social_account_id:
            followers_start = await _followers_at_or_before(
                db, r.social_account_id, r.first_at
            )
            followers_now = await _followers_latest(db, r.social_account_id)
            if followers_start is not None and followers_now is not None:
                delta = followers_now - followers_start
                conv = round(delta / r.units * 100, 1) if r.units else None
        out.append(
            {
                "event_type": r.event_type,
                "initiative": INITIATIVE_TYPES.get(r.event_type, r.event_type),
                "platform": r.platform,
                "social_account_id": str(r.social_account_id)
                if r.social_account_id
                else None,
                "events": r.events,
                "units": int(r.units or 0),
                "first_at": r.first_at,
                "last_at": r.last_at,
                "followers_start": followers_start,
                "followers_now": followers_now,
                "followers_delta": delta,
                "conversion_pct": conv,
            }
        )
    out.sort(key=lambda i: i["last_at"], reverse=True)
    return out
