"""Ops endpoints: daily Slack digest for #socialauto."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.core.config import get_settings
from app.db.session import get_db
from app.models.content import MediaAsset, Post
from app.models.queue import PublishQueue
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services import stack_ops
from app.services.paddle_digest import send_paddle_digest_to_slack
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


@router.post("/session-heal")
async def trigger_session_heal(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the session healer sweep synchronously (admin/debug).

    Probes every browser transport (LinkedIn + Facebook sidecars, shared
    bridge platforms), attempts the cheapest recovery per platform, and
    returns the per-platform status map. Long-running — the sweep can
    take several minutes while the bridge is contended.
    """
    _ = current_user
    from app.services.session_healer import heal_all_sessions

    return await heal_all_sessions()


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


class PaddleDigestResponse(BaseModel):
    text: str = ""
    posted: bool = False
    error: str | None = None


@router.post("/paddle-digest", response_model=PaddleDigestResponse)
async def trigger_paddle_digest(
    post_to_slack: bool = Query(True),
    current_user: User = Depends(get_current_user),
) -> PaddleDigestResponse:
    """Build Paddle usage/revenue digest; post to the configured #paddle channel."""
    _ = current_user
    result = await send_paddle_digest_to_slack(post_to_slack=post_to_slack)
    return PaddleDigestResponse(
        text=result.get("text", ""),
        posted=result.get("posted", False),
        error=result.get("error"),
    )


@router.get("/paddle-digest/preview", response_model=PaddleDigestResponse)
async def preview_paddle_digest(
    current_user: User = Depends(get_current_user),
) -> PaddleDigestResponse:
    """Preview the Paddle digest without posting to Slack."""
    _ = current_user
    result = await send_paddle_digest_to_slack(post_to_slack=False)
    return PaddleDigestResponse(
        text=result.get("text", ""),
        posted=False,
        error=None,
    )


@router.post("/billing-digest", response_model=PaddleDigestResponse)
async def trigger_billing_digest(
    post_to_slack: bool = Query(True),
    current_user: User = Depends(get_current_user),
) -> PaddleDigestResponse:
    """Provider-agnostic alias — posts the active provider's digest to Slack."""
    return await trigger_paddle_digest(post_to_slack=post_to_slack, current_user=current_user)


@router.get("/billing-digest/preview", response_model=PaddleDigestResponse)
async def preview_billing_digest(
    current_user: User = Depends(get_current_user),
) -> PaddleDigestResponse:
    """Provider-agnostic alias — previews the active provider's digest."""
    return await preview_paddle_digest(current_user=current_user)


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


# ---------------------------------------------------------------------------
# Ops Console — aggregated operational state for the dashboard
# ---------------------------------------------------------------------------


class ServiceStatus(BaseModel):
    name: str
    online: bool = False
    detail: str = ""


class ConsoleAccount(BaseModel):
    id: str
    platform: str
    username: str | None = None
    display_name: str | None = None
    status: str
    account_type: str = "person"
    token_expires_at: str | None = None
    audit: dict[str, Any] | None = None


class OpsConsoleResponse(BaseModel):
    checked_at: str
    services: list[ServiceStatus] = Field(default_factory=list)
    accounts: list[ConsoleAccount] = Field(default_factory=list)
    publish_queue: dict[str, int] = Field(default_factory=dict)
    media: dict[str, Any] = Field(default_factory=dict)
    browser_orchestrator: BrowserOrchestratorStatus = Field(
        default_factory=BrowserOrchestratorStatus
    )
    tiktok_audit: dict[str, Any] | None = None


class TikTokAuditUpdate(BaseModel):
    status: str = Field(
        description="Audit state, e.g. pending_review / approved / rejected / not_submitted"
    )
    reference: str | None = None
    detail: str | None = None


async def _probe_service(
    name: str, url: str, timeout: float = 4.0, container: str | None = None
) -> ServiceStatus:
    if container:
        # stack-ops fronts these services; probing the proxy would wake them.
        state = await stack_ops.service_state(container)
        if state == "stopped":
            return ServiceStatus(name=name, online=True, detail="sleeping")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return ServiceStatus(
                name=name, online=resp.status_code < 500, detail=str(resp.status_code)
            )
    except Exception as exc:
        return ServiceStatus(name=name, online=False, detail=type(exc).__name__)


async def _probe_comfyui(base: str) -> tuple[ServiceStatus, dict[str, Any]]:
    # Fronted by stack-ops — don't wake a sleeping GPU service just to probe it.
    if await stack_ops.is_asleep("social-media-comfyui-gpu"):
        return (
            ServiceStatus(name="comfyui", online=True, detail="sleeping"),
            {},
        )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            stats_resp, queue_resp = await asyncio.gather(
                client.get(f"{base}/system_stats"),
                client.get(f"{base}/queue"),
            )
            queue_info: dict[str, Any] = {}
            if queue_resp.status_code == 200:
                qdata = queue_resp.json()
                queue_info = {
                    "pending": len(qdata.get("queue_pending", [])),
                    "running": len(qdata.get("queue_running", [])),
                }
            return (
                ServiceStatus(
                    name="comfyui",
                    online=stats_resp.status_code == 200,
                    detail=str(stats_resp.status_code),
                ),
                queue_info,
            )
    except Exception as exc:
        return ServiceStatus(name="comfyui", online=False, detail=type(exc).__name__), {}


@router.get("/console", response_model=OpsConsoleResponse)
async def ops_console(
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OpsConsoleResponse:
    """Aggregated operational state for the Ops Console dashboard.

    Combines sidecar/service health probes, connected-account status,
    publish-queue counts, ComfyUI media-job state, browser-orchestrator
    status, and the recorded TikTok Direct Post audit state — one call so
    the dashboard (and the cloudless.gr admin proxy) never has to fan out.
    """
    _ = current_user
    settings = get_settings()

    sidecar_checks = [
        ("browser-bridge", f"{settings.BROWSER_BRIDGE_URL}/health", "browser-novnc"),
        ("linkedin-sidecar", f"{settings.LINKEDIN_BROWSER_SIDECAR_URL}/health", "linkedin-browser-sidecar"),
        ("facebook-sidecar", f"{settings.FACEBOOK_BROWSER_SIDECAR_URL}/health", "facebook-browser-sidecar"),
        ("tiktok-sidecar", f"{settings.TIKTOK_BROWSER_SIDECAR_URL}/health", "tiktok-browser-sidecar"),
    ]
    probes, (comfy_status, comfy_queue) = await asyncio.gather(
        asyncio.gather(*(_probe_service(n, u, container=c) for n, u, c in sidecar_checks)),
        _probe_comfyui(settings.COMFYUI_URL.rstrip("/")),
    )
    services = [*probes, comfy_status]

    accounts_result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.team_id == team_id)
        .order_by(SocialAccount.platform, SocialAccount.username)
    )
    accounts = [
        ConsoleAccount(
            id=str(acc.id),
            platform=acc.platform,
            username=acc.username,
            display_name=acc.display_name,
            status=acc.status,
            account_type=acc.account_type,
            token_expires_at=acc.token_expires_at.isoformat()
            if acc.token_expires_at
            else None,
            audit=(acc.meta_data or {}).get("audit"),
        )
        for acc in accounts_result.scalars().all()
    ]

    queue_counts = await db.execute(
        select(PublishQueue.status, func.count())
        .join(PublishQueue.post)
        .where(Post.team_id == team_id)
        .group_by(PublishQueue.status)
    )
    publish_queue = {
        str(status.value if hasattr(status, "value") else status): count
        for status, count in queue_counts.all()
    }

    recent_media = await db.execute(
        select(func.count())
        .select_from(MediaAsset)
        .where(MediaAsset.team_id == team_id, MediaAsset.source == "ai-generated")
    )
    media = {
        "ai_generated_assets": recent_media.scalar() or 0,
        "comfyui_queue": comfy_queue,
    }

    orchestrator = BrowserOrchestratorStatus()
    try:
        from app.services.browser_orchestrator import get_current_platform, get_queue_length

        platform = await get_current_platform()
        orchestrator = BrowserOrchestratorStatus(
            current_platform=platform,
            queue_length=await get_queue_length(),
            lock_held=platform is not None,
            message=f"Browser held by {platform}" if platform else "Browser idle",
        )
    except Exception:
        orchestrator.message = "Orchestrator status unavailable"

    tiktok_audit = next(
        (a.audit for a in accounts if a.platform == "tiktok" and a.audit), None
    )

    return OpsConsoleResponse(
        checked_at=datetime.now(UTC).isoformat(),
        services=services,
        accounts=accounts,
        publish_queue=publish_queue,
        media=media,
        browser_orchestrator=orchestrator,
        tiktok_audit=tiktok_audit,
    )


@router.put("/tiktok-audit", response_model=dict[str, Any])
async def update_tiktok_audit(
    body: TikTokAuditUpdate,
    team_id: TeamId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record the TikTok Direct Post audit state on the team's TikTok accounts.

    Written by the console-ops tooling (and the Ops Console UI) after
    checking the TikTok developer portal, so the dashboard reflects the
    real review state instead of a stale assumption.
    """
    _ = current_user
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id, SocialAccount.platform == "tiktok"
        )
    )
    accounts = result.scalars().all()
    if not accounts:
        return {"updated": 0, "message": "no TikTok accounts connected"}

    stamp = {
        "status": body.status,
        "reference": body.reference,
        "detail": body.detail,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    for acc in accounts:
        meta = dict(acc.meta_data or {})
        meta["audit"] = stamp
        acc.meta_data = meta
    await db.commit()
    return {"updated": len(accounts), "audit": stamp}
