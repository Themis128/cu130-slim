"""Celery task — check and refresh LinkedIn browser sidecar session.

Runs every 12 hours. Calls the linkedin-browser-sidecar's ``GET /session``
endpoint to verify the browser session is still alive. If alive, exports
the fresh cookies and stores them in the ``LINKEDIN_COOKIE`` env var via
the secret store so other services can reuse them. If expired, marks the
account as ``expired`` and sends an alert email to the team owner.

The task is non-fatal — failures are logged but do not abort the loop.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError
from app.services.slack_notifications import post_alert_to_slack
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

_ALERT_COOLDOWN_HOURS = 24


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _send_alert(owner: User, reason: str) -> None:
    """Send an alert email about an expired LinkedIn session. Cooldown 24h."""
    try:
        from datetime import timedelta

        from sqlalchemy import func

        from app.models.email_log import EmailLog

        async with async_sessionmaker(
            create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool),
            class_=AsyncSession,
            expire_on_commit=False,
        ) as db:
            cutoff = datetime.now(UTC) - timedelta(hours=_ALERT_COOLDOWN_HOURS)
            recent = await db.execute(
                select(func.count(EmailLog.id)).where(
                    EmailLog.recipient == owner.email,
                    EmailLog.template == "linkedin_session_alert",
                    EmailLog.created_at >= cutoff,
                )
            )
            if recent.scalar_one() > 0:
                logger.info("Skipping LI alert for %s — within cooldown", owner.email)
                return

        # Slack alert (best-effort; uses same cooldown gating as email).
        await post_alert_to_slack(
            "*LinkedIn session expired*\n"
            f"• reason: {reason[:500]}\n"
            "_Action: re-login in LinkedIn sidecar and re-capture session_"
        )

        from app.services.email_templates import send_linkedin_session_alert_email

        await send_linkedin_session_alert_email(
            owner_email=owner.email,
            owner_name=owner.name or "",
            reason=reason,
        )
    except Exception:
        logger.debug("LI alert email failed (non-fatal)", exc_info=True)


async def _run_check() -> dict:
    """Check LinkedIn sidecar session and refresh cookies if alive."""
    settings = get_settings()
    sidecar_url = settings.LINKEDIN_BROWSER_SIDECAR_URL
    summary = {"sidecar_up": False, "session_alive": False, "cookies_refreshed": False, "accounts_marked": 0}

    client = LinkedInSidecarClient(base_url=sidecar_url)

    # Check sidecar health
    try:
        health = await client.health()
        summary["sidecar_up"] = health.get("status") == "ok"
    except Exception as exc:
        logger.error("LinkedIn sidecar at %s is not responding: %s", sidecar_url, exc)
        return summary

    if not summary["sidecar_up"]:
        logger.error("LinkedIn sidecar is not healthy")
        return summary

    # Check and refresh session
    try:
        result = await client.refresh_session()
        summary["session_alive"] = result.get("logged_in", False)
        cookies = result.get("cookies", {})
        if cookies:
            summary["cookies_refreshed"] = True
            # Persist the refreshed li_at cookie to the secret store.
            li_at = cookies.get("li_at", "")
            if li_at:
                try:
                    from app.services.secret_store import secret_store

                    await secret_store.set("LINKEDIN_COOKIE", li_at, "LinkedIn li_at session cookie (auto-refreshed)")
                    logger.info("LinkedIn cookie refreshed and saved to secret store")
                except Exception as exc:
                    logger.warning("Could not save refreshed LinkedIn cookie: %s", exc)
    except LinkedInSidecarError as exc:
        logger.warning("LinkedIn session refresh failed: %s", exc)
        summary["session_alive"] = False

    # Mark LinkedIn accounts as expired if session is dead
    if not summary["session_alive"]:
        async with _worker_db() as db:
            result = await db.execute(
                select(SocialAccount).where(SocialAccount.platform == "linkedin")
            )
            accounts = result.scalars().all()
            for account in accounts:
                if account.status != "expired":
                    account.status = "expired"
                    summary["accounts_marked"] += 1

                    # Alert the team owner.
                    owner_result = await db.execute(
                        select(User).join(Team, Team.owner_id == User.id).where(Team.id == account.team_id)
                    )
                    owner = owner_result.scalar_one_or_none()
                    if owner is not None:
                        await _send_alert(owner, "LinkedIn browser session expired — re-capture required")
            await db.commit()

    logger.info("LinkedIn session check complete: %s", summary)
    return summary


@shared_task(name="app.worker.tasks.linkedin_session_check.check_linkedin_sessions")
def check_linkedin_sessions() -> dict:
    """Periodic task — check and refresh LinkedIn sidecar session (every 12h)."""
    return asyncio.run(_run_check())
