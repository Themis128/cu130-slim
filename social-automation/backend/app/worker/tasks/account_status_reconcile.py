"""Celery task — reconcile social_accounts.status against live platform truth.

Runs every 6h. The DB `status` column is write-once at connect time and drifts
(observed: WhatsApp showed ``active`` while WABA reported NOT_VERIFIED, a
revoked duplicate FB page row sat next to a working one). For each account we
probe the platform's authoritative endpoint with the decrypted token and:

- ``last_verified_at`` is stamped on every probe (success or not)
- ``status`` becomes ``active`` / ``expired`` / ``revoked`` / ``error``
- A Slack alert fires when an account flips INTO a bad state (drift, not noise)

Per-platform probes use the cheapest authoritative read:
- Meta (facebook/instagram/threads/whatsapp): Graph API object GET
- TikTok: creator_info query   - LinkedIn: /v2/userinfo
- Twitter/X: /2/users/me       - Telegram: getMe
Accounts whose ``token_expires_at`` already passed are marked ``expired``
without a network probe. A single account failure never aborts the sweep.
"""
import logging
from datetime import UTC, datetime

import httpx
from celery import shared_task
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.slack_notifications import post_alert_to_slack
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


async def _probe(platform: str, account_id: str, token: str, meta: dict | None) -> str:
    """Return 'active' | 'revoked' | 'inconclusive' from a live platform read."""
    meta = meta or {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            if platform == "threads":
                r = await c.get(
                    "https://graph.threads.net/v1.0/me",
                    params={"fields": "id", "access_token": token},
                )
            elif platform == "instagram":
                if meta.get("login_type") == "business_login":
                    url = "https://graph.instagram.com/v21.0/me"
                else:
                    url = f"https://graph.facebook.com/v21.0/{account_id}"
                r = await c.get(url, params={"fields": "id", "access_token": token})
            elif platform == "facebook":
                r = await c.get(
                    f"https://graph.facebook.com/v21.0/{account_id}",
                    params={"fields": "id", "access_token": token},
                )
            elif platform == "whatsapp":
                phone_id = meta.get("phone_number_id")
                if not phone_id:
                    return "inconclusive"
                r = await c.get(
                    f"https://graph.facebook.com/v21.0/{phone_id}",
                    params={"fields": "code_verification_status", "access_token": token},
                )
                if r.status_code == 200:
                    cvs = (r.json() or {}).get("code_verification_status")
                    if cvs in (None, "VERIFIED", "EXPIRED"):
                        return "active"
                    if cvs == "NOT_VERIFIED":
                        return "revoked"
                    return "inconclusive"
            elif platform == "tiktok":
                r = await c.post(
                    "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={},
                )
                if r.status_code == 200:
                    err = (r.json().get("error") or {}).get("code", "ok")
                    return "active" if err == "ok" else "revoked"
                return "revoked" if r.status_code in (401, 403) else "inconclusive"
            elif platform == "linkedin":
                r = await c.get(
                    "https://api.linkedin.com/v2/userinfo",
                    headers={"Authorization": f"Bearer {token}"},
                )
            elif platform == "twitter":
                r = await c.get(
                    "https://api.x.com/2/users/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 401:
                    return "inconclusive"
            elif platform == "telegram":
                r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
            else:
                return "unprobed"
            if r.status_code == 200:
                return "active"
            if r.status_code in (400, 401, 403):
                return "revoked"
            return "inconclusive"
    except Exception as exc:
        logger.warning("probe %s failed: %s", platform, type(exc).__name__)
        return "inconclusive"


async def _reconcile() -> dict[str, int]:
    counts = {"checked": 0, "drifted": 0, "active": 0, "expired": 0, "revoked": 0, "error": 0}
    now = datetime.now(UTC)
    async with task_session() as db:
        rows = (await db.execute(select(SocialAccount))).scalars().all()
        for acc in rows:
            counts["checked"] += 1
            new_status: str | None = None
            if acc.token_expires_at and acc.token_expires_at.replace(tzinfo=UTC) < now:
                new_status = "expired"
            elif acc.access_token_enc:
                try:
                    token = decrypt_token(acc.access_token_enc)
                except Exception:
                    token = ""
                new_status = await _probe(acc.platform, acc.account_id, token, acc.meta_data) if token else "inconclusive"
            if new_status in ("inconclusive", "error"):
                acc.meta_data = {**(acc.meta_data or {}), "last_verify_attempt_at": now.isoformat()}
                counts["error"] += 1
                continue
            if new_status in (None, "unprobed"):
                continue
            counts[new_status] += 1
            acc.meta_data = {**(acc.meta_data or {}), "last_verified_at": now.isoformat(),
                             "token_probe_status": new_status}
            if acc.status != new_status:
                if new_status == "active" and acc.status in ("expired", "revoked"):
                    logger.info("token OK but not promoting %s %s — session health owns %s",
                                acc.platform, acc.username, acc.status)
                    continue
                logger.info("drift: %s %s status %s -> %s", acc.platform, acc.username, acc.status, new_status)
                counts["drifted"] += 1
                if new_status in ("expired", "revoked") and acc.status == "active":
                    await post_alert_to_slack(
                        f":warning: Account drift — {acc.platform} `{acc.username or acc.account_id}` "
                        f"is *{new_status}* on the platform but was `active` in DB. Reconnect needed."
                    )
                acc.status = new_status
                try:
                    from app.services.db_router import db_router
                    await db_router.execute(
                        "UPDATE social_accounts SET status = ? WHERE id = ?",
                        [new_status, str(acc.id)])
                except Exception:
                    logger.debug("D1 mirror skipped for %s", acc.id, exc_info=True)
        await db.commit()
    return counts


@shared_task(name="app.worker.tasks.account_status_reconcile.reconcile_account_status")
def reconcile_account_status() -> dict[str, int]:
    return run_async(_reconcile())
