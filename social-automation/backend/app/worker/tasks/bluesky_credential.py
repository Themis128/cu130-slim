"""Bluesky credential-rotation sweep.

The stored Bluesky credential is an app password. When publishes start
failing with ``401 AuthenticationRequired`` the app password is dead
(revoked on password change / bsky-side invalidation) — only a human can
mint a new one in the bsky.app UI… unless the account MAIN password is
available as ``BSKY_MAIN_PASSWORD``: then ``createAppPassword`` rotates it
fully unattended (app passwords cannot mint app passwords — full-account
scope required, atproto PR #826).

Beat cadence: daily. Rotates only when recent failed targets carry the
401 signature; no-op otherwise. Alerts once when rotation is needed but
``BSKY_MAIN_PASSWORD`` is not configured.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

_401_MARKER = "AuthenticationRequired"
_LOOKBACK_H = 24


async def _sweep() -> dict:
    from app.models.content import PostTarget
    from app.models.social_account import SocialAccount
    from app.services.bluesky_api import BlueskyAPIError, rotate_app_password
    from app.services.slack_notifications import send_telegram_alert

    main_password = os.environ.get("BSKY_MAIN_PASSWORD") or ""
    async with task_session() as db:
        account = (
            await db.execute(select(SocialAccount).where(SocialAccount.platform == "bluesky"))
        ).scalars().first()
        if account is None:
            return {"skipped": "no bluesky account"}

        since = datetime.now(UTC) - timedelta(hours=_LOOKBACK_H)
        recent_401 = (
            await db.execute(
                select(PostTarget.post_id)
                .where(PostTarget.social_account_id == account.id)
                .where(PostTarget.status == "failed")
                .where(PostTarget.updated_at >= since)
                .where(PostTarget.error_message.ilike(f"%{_401_MARKER}%"))
                .limit(1)
            )
        ).first()
        if recent_401 is None:
            return {"rotated": False, "reason": "no recent 401 auth failures"}

        if not main_password:
            await send_telegram_alert(
                "ops",
                "bluesky-credential: app password rejected (401) but "
                "BSKY_MAIN_PASSWORD is not set — add it to .env/Infisical to "
                "enable unattended app-password rotation, or mint a new app "
                "password at bsky.app → Settings → App passwords.",
            )
            return {"rotated": False, "reason": "BSKY_MAIN_PASSWORD not configured"}

        try:
            result = await rotate_app_password(account, main_password, db)
        except BlueskyAPIError as exc:
            await send_telegram_alert(
                "ops",
                f"bluesky-credential: rotation failed ({exc.status_code}): "
                f"{exc.response_text[:200]}",
            )
            return {"rotated": False, "error": str(exc)[:200]}

        await send_telegram_alert(
            "ops",
            "bluesky-credential: app password rotated automatically after "
            "401 auth failures — publishes resume on next retry sweep.",
        )
        logger.info("bluesky app password rotated for account %s", account.id)
        return result


@shared_task(
    name="app.worker.tasks.bluesky_credential.run_bluesky_credential_sweep",
    max_retries=0,
)
def run_bluesky_credential_sweep():
    return run_async(_sweep())
