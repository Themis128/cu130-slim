"""Celery task — auto-refresh expiring OAuth tokens for all social accounts.

TikTok access tokens expire in 24 hours, Twitter in 2 hours, and Meta/Threads
in ~60 days. This task runs every hour and refreshes any token that will expire
within the next 4 hours, so accounts never go offline unexpectedly.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models.social_account import SocialAccount
from app.services.db_sync import sync_after_worker_task
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

# Refresh tokens that will expire within this window
REFRESH_WINDOW = timedelta(hours=4)
# Don't refresh tokens more often than this
MIN_REFRESH_INTERVAL = timedelta(hours=1)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _get_oauth_client(platform: str):
    """Return the OAuth2 client for a platform, or None if not refreshable."""
    # Import here to avoid circular imports at module load time
    from app.api.auth import (
        facebook_client,
        instagram_client,
        linkedin_client,
        threads_client,
        tiktok_client,
        twitter_client,
    )

    clients = {
        "linkedin": linkedin_client,
        "twitter": twitter_client,
        "facebook": facebook_client,
        "instagram": instagram_client,
        "threads": threads_client,
        "tiktok": tiktok_client,
    }
    return clients.get(platform)


@shared_task(name="app.worker.tasks.token_refresh.refresh_expiring_tokens")
def refresh_expiring_tokens() -> dict:
    """Refresh all social account tokens that will expire within 4 hours."""
    result = asyncio.run(_refresh_expiring_tokens_async())
    # Push updated social_accounts to D1
    if result.get("refreshed", 0) > 0:
        asyncio.run(sync_after_worker_task(["social_accounts"]))
    return result


async def _refresh_expiring_tokens_async() -> dict:
    summary: dict = {"checked": 0, "refreshed": 0, "skipped": 0, "errors": []}
    now = datetime.now(UTC)
    cutoff = now + REFRESH_WINDOW

    async with _worker_db() as db:
        # Find all active accounts with a refresh token that will expire soon
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.status == "active",
                SocialAccount.refresh_token_enc.isnot(None),
                SocialAccount.token_expires_at.isnot(None),
                SocialAccount.token_expires_at <= cutoff,
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            summary["checked"] += 1
            platform = account.platform
            account_label = f"{platform}/{account.username or account.account_id}"

            # Skip if we refreshed too recently
            if account.updated_at and (now - account.updated_at.replace(tzinfo=UTC)) < MIN_REFRESH_INTERVAL:
                logger.info("Skipping %s — refreshed recently (%s)", account_label, account.updated_at)
                summary["skipped"] += 1
                continue

            client = _get_oauth_client(platform)
            if client is None:
                logger.warning("No OAuth client for platform %s — skipping", platform)
                summary["skipped"] += 1
                continue

            try:
                refresh_token = decrypt_token(account.refresh_token_enc)
                token = await client.refresh_token(refresh_token)
            except Exception as exc:
                logger.exception("Token refresh failed for %s: %s", account_label, exc)
                summary["errors"].append(f"{account_label}: {exc}")
                # Mark as expired if refresh fails
                account.status = "expired"
                await db.commit()
                continue

            new_access = token.get("access_token", "")
            new_refresh = token.get("refresh_token")
            if not new_access:
                msg = f"{account_label}: no access_token in refresh response"
                logger.error(msg)
                summary["errors"].append(msg)
                account.status = "expired"
                await db.commit()
                continue

            # Update tokens
            account.access_token_enc = encrypt_token(new_access)
            if new_refresh:
                account.refresh_token_enc = encrypt_token(new_refresh)
            account.status = "active"

            # Update expiry
            expires_in = token.get("expires_in")
            if expires_in:
                account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
            else:
                # TikTok doesn't return expires_in on refresh — default to 24h
                if platform == "tiktok":
                    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=86400)
                # Meta long-lived tokens are 60 days
                elif platform in ("facebook", "instagram", "threads"):
                    account.token_expires_at = datetime.now(UTC) + timedelta(days=60)
                else:
                    account.token_expires_at = None

            await db.commit()
            summary["refreshed"] += 1
            logger.info(
                "Refreshed %s — new expiry: %s",
                account_label,
                account.token_expires_at,
            )

    logger.info(
        "Token refresh complete: checked=%d refreshed=%d skipped=%d errors=%d",
        summary["checked"],
        summary["refreshed"],
        summary["skipped"],
        len(summary["errors"]),
    )
    return summary
