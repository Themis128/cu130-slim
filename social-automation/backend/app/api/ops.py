"""Ops endpoints: daily Slack digest for #socialauto."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services.slack_digest import run_daily_digest_for_all_teams
from app.worker.tasks.digest import send_daily_slack_digest

router = APIRouter()


class DailyDigestResponse(BaseModel):
    reports: list[dict[str, Any]] = Field(default_factory=list)
    queued: bool = False
    message: str = ""


@router.post("/daily-digest", response_model=DailyDigestResponse)
async def trigger_daily_digest(
    days: int = Query(1, ge=1, le=30),
    post_to_slack: bool = Query(True),
    post_to_email: bool = Query(True),
    async_queue: bool = Query(
        False,
        description="If true, enqueue Celery task instead of running inline",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyDigestResponse:
    """Build analytics + issues digest; post to Slack #socialauto and/or email."""
    _ = current_user
    if async_queue:
        send_daily_slack_digest.delay(
            days=days, post_to_slack=post_to_slack, post_to_email=post_to_email
        )
        return DailyDigestResponse(
            queued=True,
            message="Daily digest queued on social-worker",
        )

    reports = await run_daily_digest_for_all_teams(
        db, days=days, post_to_slack=post_to_slack, post_to_email=post_to_email
    )
    posted = sum(1 for r in reports if r.get("posted_to_slack"))
    emailed = sum(1 for r in reports if r.get("emailed"))
    errors = [
        r.get("slack_error") or r.get("email_error")
        for r in reports
        if r.get("slack_error") or r.get("email_error")
    ]
    msg = f"Built {len(reports)} report(s); Slack {posted}; email {emailed}"
    if errors:
        msg += f"; issues: {errors[0]}"
    return DailyDigestResponse(reports=reports, message=msg)


@router.get("/daily-digest/preview", response_model=DailyDigestResponse)
async def preview_daily_digest(
    days: int = Query(1, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyDigestResponse:
    """Preview digest without posting to Slack or email."""
    _ = current_user
    reports = await run_daily_digest_for_all_teams(
        db, days=days, post_to_slack=False, post_to_email=False
    )
    return DailyDigestResponse(
        reports=reports,
        message="Preview only (not posted)",
    )
