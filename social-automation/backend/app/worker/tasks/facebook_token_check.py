"""Celery task — Meta Graph token health.

Facebook User/Page tokens don't refresh automatically like IG's.
This task probes each active Facebook/Threads account token every 6h
and raises a P2 Telegram alert when a token is expired/invalid or dies
within 7 days — replacing the legacy external 'FB heal' monitor with a
platform-governed worker.

Facebook tokens are introspected via Graph /debug_token with the
Facebook app token. Threads tokens CANNOT be introspected that way —
they are issued by a different app — so they are probed with
GET graph.threads.net/v1.0/me instead. Responses that error without a
usable payload (transient Meta code 2, wrong-app introspection, etc.)
are inconclusive, never treated as invalid.

Revoked/retired rows (e.g. deleted platform profiles) are skipped:
they are already known-bad and were alerted at revocation time.

Lane: P2 (ops) — reconnect is manual (Accounts UI OAuth).
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v26.0"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
WARN_DAYS = 7
DEAD_TOKEN_CODES = (190, 102, 463, 467)
# Redis-side dedup key baked into telegram_alert's own pattern: alert once/day.


@celery_app.task(name="app.worker.tasks.facebook_token_check.check_facebook_tokens")
def check_facebook_tokens() -> dict:
    return run_async(_check())


async def _probe_token(acct: SocialAccount, token: str, app_token: str) -> tuple[bool | None, int]:
    """Return (is_valid, expires_at_unix).

    is_valid None = inconclusive (transient Meta error, unusable payload)
    — never flagged invalid on an error body.
    """
    async with httpx.AsyncClient(timeout=15) as c:
        if acct.platform == "threads":
            # Threads tokens are issued by the Threads app; FB /debug_token
            # cannot introspect them. Probe the token directly instead.
            r = await c.get(
                f"{THREADS_GRAPH}/me",
                params={"fields": "id", "access_token": token},
            )
            body = r.json()
            if r.status_code == 200 and body.get("id"):
                return True, 0
            err = (body.get("error") or {}).get("code")
            return (False if err in DEAD_TOKEN_CODES else None), 0

        r = await c.get(
            f"{GRAPH}/debug_token",
            params={"input_token": token, "access_token": app_token},
        )
        body = r.json()
        d = body.get("data")
        if d is None:
            # A top-level error here is about the app token or the request
            # itself (e.g. rotated FACEBOOK_CLIENT_*), not the input token —
            # an invalid input_token comes back as data.is_valid=false.
            # Flagging it dead would mass-alert every FB account.
            return None, 0
        return bool(d.get("is_valid")), int(d.get("expires_at") or 0)


async def _check() -> dict:
    stats = {"scanned": 0, "healthy": 0, "expiring": 0, "expired": 0, "inconclusive": 0}
    async with task_session() as db:
        rows = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform.in_(["facebook", "threads"]),
                        SocialAccount.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )

        settings = get_settings()
        app_token = f"{settings.FACEBOOK_CLIENT_ID}|{settings.FACEBOOK_CLIENT_SECRET}"
        for acct in rows:
            stats["scanned"] += 1
            meta = acct.meta_data or {}
            raw = acct.access_token_enc
            if not raw:
                continue
            token = decrypt_token(bytes(raw))
            try:
                valid, exp = await _probe_token(acct, token, app_token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Meta token probe failed %s: %s", acct.id, exc)
                continue

            meta["fb_token_checked_at"] = datetime.now(UTC).isoformat()
            if valid is None:
                stats["inconclusive"] += 1
            elif not valid:
                stats["expired"] += 1
                meta["fb_token_invalid"] = True
                await send_telegram_alert(
                    "ops",
                    f"Meta token invalid/expired for {acct.platform} @{acct.username} — reconnect via Accounts UI OAuth (manual).",
                )
            else:
                meta["fb_token_invalid"] = False
                # Threads /me gives no expiry — fall back to the stored value.
                if acct.platform == "threads" and not exp and acct.token_expires_at:
                    exp = int(acct.token_expires_at.timestamp())
                days = (datetime.fromtimestamp(exp, UTC) - datetime.now(UTC)).days if exp else 999
                if exp and days <= WARN_DAYS:
                    stats["expiring"] += 1
                    await send_telegram_alert(
                        "ops",
                        f"Meta token for {acct.platform} @{acct.username} expires in {days}d — reconnect via Accounts UI OAuth soon.",
                    )
                else:
                    stats["healthy"] += 1
            acct.meta_data = meta
            flag_modified(acct, "meta_data")
        await db.commit()
    return stats
