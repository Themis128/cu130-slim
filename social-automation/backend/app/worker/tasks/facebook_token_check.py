"""Celery task — Facebook Graph token health.

Facebook User/Page tokens don't refresh automatically like IG's.
This task introspects each Facebook/Threads account token via
/debug_token every 6h and raises a P2 Telegram alert when a token is
expired/invalid or dies within 7 days — replacing the legacy external
'FB heal' monitor with a platform-governed worker.

Lane: P2 (ops) — reconnect is manual (Accounts UI OAuth).
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
WARN_DAYS = 7
# Redis-side dedup key baked into telegram_alert's own pattern: alert once/day.


@celery_app.task(name="app.worker.tasks.facebook_token_check.check_facebook_tokens")
def check_facebook_tokens() -> dict:
    return run_async(_check())


async def _check() -> dict:
    stats = {"scanned": 0, "healthy": 0, "expiring": 0, "expired": 0}
    async with task_session() as db:
        rows = (
            await db.execute(
                select(SocialAccount).where(SocialAccount.platform.in_(["facebook", "threads"]))
            )
        ).scalars().all()

        for acct in rows:
            stats["scanned"] += 1
            meta = acct.meta_data or {}
            app_tok = meta.get("app_access_token") or ""
            raw = acct.access_token or ""
            if not raw or not app_tok:
                continue
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"{GRAPH}/debug_token",
                        params={"input_token": decrypt_token(raw), "access_token": decrypt_token(app_tok)},
                    )
                d = (r.json().get("data") or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("FB debug_token failed %s: %s", acct.id, exc)
                continue

            meta["fb_token_checked_at"] = datetime.now(UTC).isoformat()
            if not d.get("is_valid"):
                stats["expired"] += 1
                meta["fb_token_invalid"] = True
                await send_telegram_alert(
                    "ops",
                    f"FB token invalid/expired for {acct.platform} @{acct.username} — "
                    "reconnect via Accounts UI OAuth (manual).",
                )
            else:
                meta["fb_token_invalid"] = False
                exp = d.get("expires_at") or 0
                days = (datetime.fromtimestamp(exp, UTC) - datetime.now(UTC)).days if exp else 999
                if exp and days <= WARN_DAYS:
                    stats["expiring"] += 1
                    await send_telegram_alert(
                        "ops",
                        f"FB token for {acct.platform} @{acct.username} expires in {days}d — "
                        "reconnect via Accounts UI OAuth soon.",
                    )
                else:
                    stats["healthy"] += 1
            acct.meta_data = meta
            flag_modified(acct, "meta_data")
        await db.commit()
    return stats
