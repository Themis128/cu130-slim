"""Celery task — check Instagram private-API sidecar session health.

Runs every 6 hours. For each Instagram ``social_account`` that has a
``private_api_session_id`` in its ``meta_data``, the task calls the
aiograpi-rest sidecar's ``GET /account`` endpoint with the ``X-Session-ID``
header. If the session is expired or the sidecar is unreachable, the account
status is set to ``expired`` and an alert email is sent to the team owner.

The task is non-fatal — a single account failure does not abort the loop.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_field
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.slack_notifications import post_alert_to_slack
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

# Alert cooldown — don't email the same owner more than once per day per account.
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


async def _check_sidecar_session(session_id: str, sidecar_url: str) -> bool:
    """Return True if the session is valid (GET /account succeeds)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{sidecar_url.rstrip('/')}/account",
                headers={"X-Session-ID": session_id},
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Instagram sidecar unreachable: %s", exc)
        return False


async def _check_sidecar_health(sidecar_url: str) -> bool:
    """Return True if the sidecar process itself is up."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{sidecar_url.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _send_alert(owner: User, account_username: str, reason: str) -> None:
    """Send an alert email about an expired Instagram session. Cooldown 24h."""
    try:
        from app.models.email_log import EmailLog

        # Check cooldown — skip if we already alerted in the last 24h.
        async with async_sessionmaker(
            create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool),
            class_=AsyncSession,
            expire_on_commit=False,
        ) as db:
            from datetime import timedelta

            cutoff = datetime.now(UTC) - timedelta(hours=_ALERT_COOLDOWN_HOURS)
            from sqlalchemy import func

            recent = await db.execute(
                select(func.count(EmailLog.id)).where(
                    EmailLog.recipient == owner.email,
                    EmailLog.template == "instagram_session_alert",
                    EmailLog.created_at >= cutoff,
                )
            )
            if recent.scalar_one() > 0:
                logger.info("Skipping IG alert for %s — within cooldown", owner.email)
                return

        # Slack alert (best-effort; uses same cooldown gating as email).
        await post_alert_to_slack(
            "\n".join(
                [
                    "*Instagram needs you to reconnect*",
                    f"• Account: `@{account_username}`",
                    "• What to do next: go to Settings → Accounts and re-connect Instagram.",
                    f"• What happened: {(reason or '').replace(chr(10), ' ').strip()[:300] or 'Session check failed'}",
                    "_Cloudless · Clear skies. Zero friction._",
                ]
            )[:2000]
        )

        from app.services.email_templates import send_instagram_session_alert_email

        await send_instagram_session_alert_email(
            owner_email=owner.email,
            owner_name=owner.name or "",
            account_username=account_username,
            reason=reason,
        )
    except Exception:
        logger.debug("IG alert email failed (non-fatal)", exc_info=True)


async def _run_check() -> dict:
    """Check all Instagram sessions and return a summary."""
    settings = get_settings()
    sidecar_url = settings.INSTAGRAM_PRIVATE_API_URL
    summary = {"checked": 0, "healthy": 0, "expired": 0, "sidecar_up": False}

    summary["sidecar_up"] = await _check_sidecar_health(sidecar_url)
    if not summary["sidecar_up"]:
        logger.error("Instagram sidecar at %s is not responding", sidecar_url)

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == "instagram")
        )
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            session_id_enc = meta.get("private_api_session_id")
            if not session_id_enc:
                continue
            session_id = decrypt_field(session_id_enc) or ""
            if not session_id:
                continue

            summary["checked"] += 1
            healthy = await _check_sidecar_session(session_id, sidecar_url)
            if healthy:
                summary["healthy"] += 1
                if account.status == "expired":
                    account.status = "active"
                    await db.commit()
                logger.info("IG session for @%s is healthy", account.username)
            else:
                summary["expired"] += 1
                if account.status != "expired":
                    account.status = "expired"
                    await db.commit()
                logger.warning("IG session for @%s is expired", account.username)

                # Alert the team owner.
                owner_result = await db.execute(
                    select(User).join(Team, Team.owner_id == User.id).where(Team.id == account.team_id)
                )
                owner = owner_result.scalar_one_or_none()
                if owner is not None:
                    reason = "sidecar unreachable" if not summary["sidecar_up"] else "session rejected"
                    await _send_alert(owner, account.username or "unknown", reason)

    logger.info("Instagram session check complete: %s", summary)
    return summary


@shared_task(name="app.worker.tasks.instagram_session_check.check_instagram_sessions")
def check_instagram_sessions() -> dict:
    """Periodic task — check all Instagram sidecar sessions (every 6h)."""
    return asyncio.run(_run_check())
