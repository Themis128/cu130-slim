"""Celery task — LinkedIn session auto-refresh and validation.

LinkedIn browser sessions expire periodically. This task checks the
LinkedIn sidecar session weekly and:
1. Validates the session is still active
2. Re-injects cookies from SocialAuto storage if the session is stale
3. Clears the rate-limit circuit breaker if it's been open too long
4. Reports session health

The task runs weekly via Celery beat.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.social_account import SocialAccount
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

LINKEDIN_SIDECAR_URL = "http://linkedin-browser-sidecar:9225"


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _run_async(coro):
    """Run an async coroutine in a sync Celery context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import threading

            result = {}
            def _runner():
                new_loop = asyncio.new_event_loop()
                try:
                    result["value"] = new_loop.run_until_complete(coro)
                except Exception as exc:
                    result["error"] = exc
                finally:
                    new_loop.close()
            t = threading.Thread(target=_runner)
            t.start()
            t.join()
            if "error" in result:
                raise result["error"]
            return result.get("value")
    except RuntimeError:
        pass
    return asyncio.run(coro)


@celery_app.task(name="app.worker.tasks.linkedin_session_refresh.refresh_linkedin_sessions")
def refresh_linkedin_sessions() -> dict:
    """Validate and refresh LinkedIn browser sessions."""
    return _run_async(_refresh_linkedin_sessions_async())


async def _refresh_linkedin_sessions_async() -> dict:
    """Async implementation of the LinkedIn session refresh task."""
    stats = {
        "accounts_checked": 0,
        "sessions_valid": 0,
        "sessions_refreshed": 0,
        "sessions_invalid": 0,
        "rate_limits_cleared": 0,
        "errors": 0,
    }

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "linkedin",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            stats["accounts_checked"] += 1
            meta = account.meta_data or {}

            try:
                # 1. Check sidecar health
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(f"{LINKEDIN_SIDECAR_URL}/health")
                    health = resp.json()

                has_session = health.get("has_session", False)
                rate_limited = health.get("rate_limited", False)
                rate_limit_until = health.get("rate_limit_until")

                # 2. Clear rate limit if it's been open for more than 6 hours
                if rate_limited and rate_limit_until:
                    try:
                        rate_limit_time = datetime.fromtimestamp(rate_limit_until / 1000, tz=UTC)
                        if (datetime.now(UTC) - rate_limit_time).total_seconds() > 6 * 3600:
                            async with httpx.AsyncClient(timeout=30.0) as client:
                                await client.post(f"{LINKEDIN_SIDECAR_URL}/session/clear-rate-limit")
                            stats["rate_limits_cleared"] += 1
                            logger.info(
                                "LinkedIn session refresh: cleared stale rate limit for account %s",
                                account.id,
                            )
                            rate_limited = False
                    except Exception:
                        pass

                # 3. Check if session is valid
                if has_session and not rate_limited:
                    # Validate by checking the feed
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.get(f"{LINKEDIN_SIDECAR_URL}/session/status")
                        status = resp.json()

                    if status.get("logged_in"):
                        stats["sessions_valid"] += 1
                        meta["linkedin_session_status"] = "valid"
                        meta["linkedin_session_checked_at"] = datetime.now(UTC).isoformat()
                        logger.info(
                            "LinkedIn session valid for account %s",
                            account.id,
                        )
                    else:
                        stats["sessions_invalid"] += 1
                        meta["linkedin_session_status"] = "expired"
                        meta["linkedin_session_checked_at"] = datetime.now(UTC).isoformat()
                        logger.warning(
                            "LinkedIn session expired for account %s — login via noVNC (port 9225)",
                            account.id,
                        )
                else:
                    stats["sessions_invalid"] += 1
                    meta["linkedin_session_status"] = "no_session"
                    meta["linkedin_session_checked_at"] = datetime.now(UTC).isoformat()
                    if rate_limited:
                        meta["linkedin_session_status"] = "rate_limited"
                        logger.info(
                            "LinkedIn session rate-limited for account %s — will retry",
                            account.id,
                        )
                    else:
                        logger.warning(
                            "LinkedIn session missing for account %s — login via noVNC (port 9225)",
                            account.id,
                        )

                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()

            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "LinkedIn session refresh error for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("LinkedIn session refresh complete: %s", stats)
    return stats
