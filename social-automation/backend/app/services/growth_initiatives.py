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

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_sanitize import sanitize_log_text
from app.models.analytics import AnalyticsEvent, FollowerSnapshot

logger = logging.getLogger(__name__)

INITIATIVE_TYPES = {
    "linkedin_page_invite": "LinkedIn Page invitation credits",
    "growth_initiative": "Custom growth initiative",
}

# LinkedIn grants Pages a pool of monthly invitation credits (shared across
# admins, renewed on the 1st). Accepted invites refund the credit; rejected
# or withdrawn ones stay spent until the reset. 100 is the common free-Page
# pool — the true balance shows in the page admin's "Invite connections"
# window and can be recorded per event via ``credits_left``.
DEFAULT_MONTHLY_CREDIT_CAP = 100


async def record_initiative_event(
    db: AsyncSession,
    team_id: UUID,
    event_type: str,
    *,
    platform: str,
    account_id: UUID | None = None,
    units: int = 1,
    note: str | None = None,
    credits_left: int | None = None,
    declined: int | None = None,
    monthly_cap: int | None = None,
) -> AnalyticsEvent:
    """Record one initiative action (e.g. 40 page invites sent).

    Optional LinkedIn-specific fields:
      - ``credits_left``: the balance shown in LinkedIn's invite window —
        the most accurate remaining-credit reading (accepted invites refund
        the credit within ~72h, so it's the ground truth).
      - ``declined``: invites the user knows were rejected/withdrawn
        (credits permanently lost this month).
      - ``monthly_cap``: override the monthly credit pool (Premium Company
        Pages get a bigger pool).
    """
    meta: dict[str, Any] = {"units": units}
    if note:
        meta["note"] = note
    if credits_left is not None:
        meta["credits_left"] = credits_left
    if declined is not None:
        meta["declined"] = declined
    if monthly_cap is not None:
        meta["monthly_cap"] = monthly_cap
    event = AnalyticsEvent(
        team_id=team_id,
        social_account_id=account_id,
        event_type=event_type,
        platform=platform,
        occurred_at=datetime.now(UTC),
        meta_data=meta,
    )
    db.add(event)
    await db.commit()
    logger.info(
        "Initiative event %s recorded: %s units on %s",
        sanitize_log_text(event_type, 80),
        units,
        sanitize_log_text(platform, 40),
    )
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
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
                func.coalesce(
                    func.sum(
                        case(
                            (
                                AnalyticsEvent.occurred_at >= month_start,
                                func.coalesce(
                                    cast(
                                        AnalyticsEvent.meta_data["units"].astext,
                                        Integer,
                                    ),
                                    1,
                                ),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("units_this_month"),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            cast(
                                AnalyticsEvent.meta_data["declined"].astext,
                                Integer,
                            ),
                            0,
                        )
                    ),
                    0,
                ).label("declined"),
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

    # Latest credits_left / monthly_cap come from the most recent event that
    # recorded them — scan the few initiative events chronologically.
    meta_rows = (
        await db.execute(
            select(AnalyticsEvent.event_type, AnalyticsEvent.meta_data)
            .where(
                AnalyticsEvent.team_id == team_id,
                AnalyticsEvent.post_id.is_(None),
                AnalyticsEvent.event_type.in_(list(INITIATIVE_TYPES)),
                AnalyticsEvent.occurred_at >= since,
            )
            .order_by(AnalyticsEvent.occurred_at)
        )
    ).all()
    latest_meta: dict[str, dict[str, Any]] = {}
    for et, md in meta_rows:
        latest_meta[et] = {**(latest_meta.get(et) or {}), **(md or {})}

    out: list[dict[str, Any]] = []
    for r in rows:
        meta = latest_meta.get(r.event_type) or {}
        monthly_cap = meta.get("monthly_cap") or DEFAULT_MONTHLY_CREDIT_CAP
        followers_start = followers_now = delta = conv = None
        if r.social_account_id:
            followers_start = await _followers_at_or_before(
                db, r.social_account_id, r.first_at
            )
            followers_now = await _followers_latest(db, r.social_account_id)
            if followers_start is not None and followers_now is not None:
                delta = followers_now - followers_start
                conv = round(delta / r.units * 100, 1) if r.units else None
        # LinkedIn credit math: every accepted invite = +1 follower and
        # refunds its credit. Accepted ≈ follower delta; pending =
        # sent − accepted − known-declined.
        units = int(r.units or 0)
        declined = int(r.declined or 0)
        accepted = delta if delta is not None else None
        pending = (
            max(0, units - (accepted or 0) - declined) if accepted is not None else None
        )
        credits_left = meta.get("credits_left")
        if credits_left is None and r.event_type == "linkedin_page_invite":
            spent_net = int(r.units_this_month or 0) - (accepted or 0)
            credits_left = min(monthly_cap, max(0, monthly_cap - spent_net))
        out.append(
            {
                "event_type": r.event_type,
                "initiative": INITIATIVE_TYPES.get(r.event_type, r.event_type),
                "platform": r.platform,
                "social_account_id": str(r.social_account_id)
                if r.social_account_id
                else None,
                "events": r.events,
                "units": units,
                "units_this_month": int(r.units_this_month or 0),
                "first_at": r.first_at,
                "last_at": r.last_at,
                "followers_start": followers_start,
                "followers_now": followers_now,
                "followers_delta": delta,
                "conversion_pct": conv,
                "accepted_est": accepted,
                "declined": declined,
                "pending_est": pending,
                "monthly_cap": monthly_cap,
                "credits_left": credits_left,
            }
        )
    out.sort(key=lambda i: i["last_at"], reverse=True)
    return out
