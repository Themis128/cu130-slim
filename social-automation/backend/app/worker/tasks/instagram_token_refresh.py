"""Celery task — Instagram access token auto-refresh.

Instagram long-lived access tokens expire after 60 days. This task
checks all Instagram accounts weekly and refreshes tokens that will
expire within 5 days via the Instagram Graph API:

    GET https://graph.instagram.com/refresh_access_token
        ?grant_type=ig_refresh_token
        &access_token=<LONG_LIVED_ACCESS_TOKEN>

Refreshed tokens are valid for 60 days from the refresh date.

This task also validates tokens by calling the /me endpoint and
marks accounts with invalid tokens for re-authentication.

The task runs weekly via Celery beat.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models.social_account import SocialAccount
from app.services.meta_graph import FACEBOOK_GRAPH_BASE, FACEBOOK_GRAPH_VERSION
from app.services.slack_notifications import post_alert_to_slack
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

GRAPH_IG_URL = "https://graph.instagram.com"
# Versioned Graph API base (Meta recommends explicit versioning).
GRAPH_FB_URL = f"{FACEBOOK_GRAPH_BASE}/{FACEBOOK_GRAPH_VERSION}"
REFRESH_THRESHOLD_DAYS = 5  # Refresh if token expires within 5 days


def _is_facebook_token(token: str) -> bool:
    """Facebook User Access Tokens start with 'EAA'; Instagram tokens start with 'IGQ'."""
    return token.startswith("EAA")


def _is_oauth_expired_error(resp: httpx.Response) -> bool:
    """Detect Meta OAuthException code 190 (session expired/invalid).

    Expired sessions return 401 on some endpoints but 400 on others
    (e.g. /media). Parse the error body instead of trusting the status.
    """
    try:
        err = resp.json().get("error") or {}
    except Exception:
        return False
    return err.get("code") == 190 or err.get("type") == "OAuthException"


async def _mark_reconnect_required(db: AsyncSession, account: SocialAccount, meta: dict, error: str) -> None:
    """Mark an account expired so the UI surfaces a reconnect prompt, and alert Slack once."""
    meta["instagram_token_status"] = "expired"
    meta["instagram_token_error"] = error[:500]
    meta["instagram_token_checked_at"] = datetime.now(UTC).isoformat()
    meta["reconnect_required"] = True
    account.meta_data = meta
    account.status = "expired"
    flag_modified(account, "meta_data")
    await db.commit()
    try:
        await post_alert_to_slack(
            "\n".join(
                [
                    "*Instagram account needs reconnection*",
                    f"• Account: @{account.username or account.account_id}",
                    "• What to do: open SocialAuto → Accounts → Instagram → Reconnect.",
                    f"• Reason: {error[:200]}",
                    "_Cloudless · Clear skies. Zero friction._",
                ]
            )[:2000]
        )
    except Exception:
        logger.debug("Slack reconnect alert failed (non-fatal)", exc_info=True)


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


@celery_app.task(name="app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens")
def refresh_instagram_tokens() -> dict:
    """Refresh Instagram long-lived access tokens before they expire."""
    return _run_async(_refresh_instagram_tokens_async())


async def _refresh_instagram_tokens_async() -> dict:
    """Async implementation of the Instagram token refresh task."""
    stats = {
        "accounts_checked": 0,
        "tokens_refreshed": 0,
        "tokens_valid": 0,
        "tokens_invalid": 0,
        "errors": 0,
        "skipped_no_token": 0,
    }

    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "instagram",
                SocialAccount.status == "active",
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            stats["accounts_checked"] += 1
            meta = account.meta_data or {}

            if not account.access_token_enc:
                stats["skipped_no_token"] += 1
                logger.info(
                    "Instagram token refresh: account %s has no access token. Skipping.",
                    account.id,
                )
                continue

            try:
                token = decrypt_token(account.access_token_enc)
            except Exception as exc:
                stats["tokens_invalid"] += 1
                logger.warning(
                    "Instagram token refresh: account %s has invalid encrypted token: %s",
                    account.id, exc,
                )
                meta["instagram_token_status"] = "invalid"
                meta["instagram_token_error"] = "decryption_failed"
                meta["instagram_token_checked_at"] = datetime.now(UTC).isoformat()
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
                continue

            # 1. Validate token by calling /me
            is_fb_token = _is_facebook_token(token)
            graph_base = GRAPH_FB_URL if is_fb_token else GRAPH_IG_URL
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{graph_base}/me",
                        params={"fields": "id,username,name" if is_fb_token else "id,username", "access_token": token},
                    )

                    if resp.status_code == 200:
                        user_data = resp.json()
                        logger.info(
                            "Instagram token valid for account %s (user: @%s)",
                            account.id, user_data.get("username", "?"),
                        )
                    elif resp.status_code in (401, 403) or _is_oauth_expired_error(resp):
                        stats["tokens_invalid"] += 1
                        logger.warning(
                            "Instagram token invalid for account %s: %d %s",
                            account.id, resp.status_code, resp.text[:200],
                        )
                        await _mark_reconnect_required(db, account, meta, resp.text)
                        continue
                    else:
                        logger.warning(
                            "Instagram token check returned %d for account %s",
                            resp.status_code, account.id,
                        )
            except Exception as exc:
                stats["errors"] += 1
                logger.warning(
                    "Instagram token validation failed for account %s: %s",
                    account.id, exc,
                )
                continue

            # 2. Check token expiry — prefer the token_expires_at column,
            #    fall back to legacy meta key.
            expires_at: datetime | None = account.token_expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at is None:
                expires_at_str = meta.get("instagram_token_expires_at")
                if expires_at_str:
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    except Exception:
                        expires_at = None
            # Backfill the column so future checks don't depend on meta.
            if expires_at is not None and account.token_expires_at is None:
                account.token_expires_at = expires_at

            needs_refresh = True
            if expires_at is not None:
                days_until_expiry = (expires_at - datetime.now(UTC)).days
                if days_until_expiry > REFRESH_THRESHOLD_DAYS:
                    needs_refresh = False
                    stats["tokens_valid"] += 1
                    logger.info(
                        "Instagram token for account %s expires in %d days — no refresh needed",
                        account.id, days_until_expiry,
                    )

            if not needs_refresh:
                meta["instagram_token_status"] = "valid"
                meta["instagram_token_checked_at"] = datetime.now(UTC).isoformat()
                meta.pop("reconnect_required", None)
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
                continue

            # 3. Refresh the token
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    if is_fb_token:
                        # Facebook User Access Token: use fb_exchange_token
                        settings = get_settings()
                        resp = await client.get(
                            f"{GRAPH_FB_URL}/oauth/access_token",
                            params={
                                "grant_type": "fb_exchange_token",
                                "client_id": settings.FACEBOOK_CLIENT_ID,
                                "client_secret": settings.FACEBOOK_CLIENT_SECRET,
                                "fb_exchange_token": token,
                            },
                        )
                    else:
                        # Instagram Access Token: use ig_refresh_token
                        resp = await client.get(
                            f"{GRAPH_IG_URL}/refresh_access_token",
                            params={
                                "grant_type": "ig_refresh_token",
                                "access_token": token,
                            },
                        )

                    if resp.status_code != 200:
                        stats["errors"] += 1
                        logger.warning(
                            "Instagram token refresh failed for account %s: %d %s",
                            account.id, resp.status_code, resp.text[:200],
                        )
                        if _is_oauth_expired_error(resp):
                            # Session already dead — ig_refresh_token can't revive it.
                            await _mark_reconnect_required(db, account, meta, resp.text)
                        else:
                            meta["instagram_token_status"] = "refresh_failed"
                            meta["instagram_token_error"] = resp.text[:500]
                            meta["instagram_token_checked_at"] = datetime.now(UTC).isoformat()
                            account.meta_data = meta
                            flag_modified(account, "meta_data")
                            await db.commit()
                        continue

                    data = resp.json()
                    new_token = data.get("access_token")
                    expires_in = data.get("expires_in", 5184000)  # 60 days default

                    if not new_token:
                        stats["errors"] += 1
                        logger.warning(
                            "Instagram token refresh returned no token for account %s",
                            account.id,
                        )
                        continue

                    # 4. Encrypt and store the new token
                    new_token_enc = encrypt_token(new_token)
                    account.access_token_enc = new_token_enc

                    new_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
                    account.token_expires_at = new_expires_at
                    meta["instagram_token_status"] = "refreshed"
                    meta["instagram_token_expires_at"] = new_expires_at.isoformat()
                    meta["instagram_token_refreshed_at"] = datetime.now(UTC).isoformat()
                    meta["instagram_token_checked_at"] = datetime.now(UTC).isoformat()
                    meta.pop("reconnect_required", None)
                    meta.pop("instagram_token_error", None)
                    account.meta_data = meta
                    flag_modified(account, "meta_data")
                    flag_modified(account, "access_token_enc")
                    await db.commit()

                    stats["tokens_refreshed"] += 1
                    logger.info(
                        "Instagram token refreshed for account %s — valid until %s",
                        account.id, new_expires_at.strftime("%Y-%m-%d"),
                    )

            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Instagram token refresh error for account %s: %s",
                    account.id, exc, exc_info=True,
                )

    logger.info("Instagram token refresh complete: %s", stats)
    return stats
