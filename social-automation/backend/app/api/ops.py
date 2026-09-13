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


class BrowserOrchestratorStatus(BaseModel):
    """Status of the browser bridge orchestrator."""
    current_platform: str | None = None
    queue_length: int = 0
    lock_held: bool = False
    lock_key: str = "browser-bridge:lock"
    queue_key: str = "browser-bridge:queue"
    platform_key: str = "browser-bridge:platform"
    message: str = ""


@router.get("/browser-orchestrator", response_model=BrowserOrchestratorStatus)
async def get_browser_orchestrator_status(
    current_user: User = Depends(get_current_user),
) -> BrowserOrchestratorStatus:
    """Get the current status of the browser bridge orchestrator.

    Shows which platform currently holds the browser lock and how many
    workers are waiting in the queue. Used by the frontend dashboard to
    visualize browser bridge coordination across messenger workers.
    """
    _ = current_user
    try:
        from app.services.browser_orchestrator import get_current_platform, get_queue_length

        platform = await get_current_platform()
        queue_len = await get_queue_length()
        return BrowserOrchestratorStatus(
            current_platform=platform,
            queue_length=queue_len,
            lock_held=platform is not None,
            message=f"Browser held by {platform}" if platform else "Browser idle",
        )
    except Exception as exc:
        return BrowserOrchestratorStatus(
            message=f"Orchestrator status unavailable: {exc}",
        )


@router.post("/browser-orchestrator/release", response_model=BrowserOrchestratorStatus)
async def force_release_browser_lock(
    current_user: User = Depends(get_current_user),
) -> BrowserOrchestratorStatus:
    """Force-release the browser bridge lock (admin/debug use only).

    Use when a worker has crashed while holding the lock and the browser
    is stuck. This clears the lock and the queue so workers can proceed.
    """
    _ = current_user
    try:
        from app.services.browser_orchestrator import force_release_lock

        released = await force_release_lock()
        return BrowserOrchestratorStatus(
            message="Lock force-released" if released else "Force-release failed",
        )
    except Exception as exc:
        return BrowserOrchestratorStatus(
            message=f"Force-release failed: {exc}",
        )


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
