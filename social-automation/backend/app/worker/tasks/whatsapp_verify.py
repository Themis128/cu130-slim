"""Celery task — WhatsApp phone number auto-verification.

This task checks all WhatsApp accounts with `code_verification_status:
NOT_VERIFIED` and attempts to request a verification code via the Meta
Cloud API. If the request succeeds (rate limit window has reset), the
task records the timestamp and notifies via Slack.

The actual code verification and registration steps require the user to
provide the 6-digit code received via SMS, so they must be done manually
via the SocialAuto API or the `auto-verify.sh` script.

The task runs every 30 minutes via Celery beat. It is a no-op when:
- All WhatsApp numbers are already VERIFIED
- The rate limit window (72h / 10 requests) is still active

Related:
- Skill: .devin/skills/whatsapp-phone-verify/SKILL.md
- Scripts: .devin/skills/whatsapp-phone-verify/scripts/
- Docs: docs/meta-services-status.md (Issue 4)
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
_settings = get_settings()

GRAPH_API_VERSION = "v26.0"
GRAPH_BASE = "https://graph.facebook.com"


@asynccontextmanager
async def _get_db():
    """Yield an async DB session."""
    engine = create_async_engine(
        _settings.DATABASE_URL,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await engine.dispose()


async def _check_phone_status(phone_id: str, token: str) -> dict:
    """Check the current verification status of a phone number."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(
            f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{phone_id}",
            params={
                "fields": "id,display_phone_number,code_verification_status,quality_rating",
                "access_token": token,
            },
        )
        if r.status_code != 200:
            return {"error": r.text[:500]}
        return r.json()


async def _request_code(phone_id: str, token: str, method: str = "SMS") -> dict:
    """Request a verification code via SMS or voice."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.post(
            f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{phone_id}/request_code",
            params={"code_method": method, "language": "en"},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = r.json()
        data["_status_code"] = r.status_code
        return data


async def _check_and_request() -> dict[str, Any]:
    """Check all WhatsApp accounts and request codes for unverified ones."""
    stats: dict[str, Any] = {
        "checked": 0,
        "already_verified": 0,
        "code_sent": 0,
        "rate_limited": 0,
        "errors": 0,
        "details": [],
    }

    async with _get_db() as db:
        from app.models.social_account import SocialAccount

        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == "whatsapp")
        )
        accounts = result.scalars().all()

        for account in accounts:
            stats["checked"] += 1
            meta = account.meta_data or {}
            if isinstance(meta, str):
                meta = json.loads(meta)

            phone_id = meta.get("phone_number_id")
            if not phone_id:
                stats["errors"] += 1
                stats["details"].append(f"{account.id}: no phone_number_id in meta_data")
                continue

            token = decrypt_token(account.access_token_enc)
            if not token:
                stats["errors"] += 1
                stats["details"].append(f"{account.id}: could not decrypt token")
                continue

            # Check current status
            status = await _check_phone_status(phone_id, token)
            if "error" in status:
                stats["errors"] += 1
                stats["details"].append(f"{account.id}: status check failed: {status['error'][:200]}")
                continue

            verification = status.get("code_verification_status", "UNKNOWN")
            if verification == "VERIFIED":
                stats["already_verified"] += 1
                stats["details"].append(f"{account.id}: already VERIFIED ✓")
                continue

            now = datetime.now(UTC)

            # ── Quota guards ─────────────────────────────────────────────
            # Meta allows 10 code requests per phone per rolling 72h. Every
            # attempt Meta sees counts — including rejected ones — so we keep
            # a per-phone request log in meta_data and prune it to the window.
            req_log = meta.get("whatsapp_code_request_log") or {}
            times: list[datetime] = []
            for raw in req_log.get(phone_id) or []:
                try:
                    t = datetime.fromisoformat(raw)
                except (ValueError, TypeError):
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=UTC)
                if now - t < timedelta(hours=72):
                    times.append(t)
            req_log[phone_id] = [t.isoformat() for t in times]
            meta["whatsapp_code_request_log"] = req_log

            def _meta_dt(key: str) -> datetime | None:
                raw = meta.get(key)
                if not raw:
                    return None
                try:
                    t = datetime.fromisoformat(raw)
                except (ValueError, TypeError):
                    return None
                return t if t.tzinfo else t.replace(tzinfo=UTC)

            skip_reason: str | None = None
            rl_at = _meta_dt("whatsapp_rate_limited_at")
            rl_phone = meta.get("whatsapp_rate_limited_phone_id")
            sent_at = _meta_dt("whatsapp_code_sent_at")
            sent_phone = meta.get("whatsapp_code_sent_phone_id")

            if len(times) >= 9:
                # Keep one slot of headroom under Meta's 10-request ceiling.
                oldest = min(times)
                skip_reason = (
                    f"72h request window saturated ({len(times)} logged, "
                    f"oldest exits {oldest + timedelta(hours=72):%Y-%m-%d %H:%M}Z)"
                )
            elif rl_at and now - rl_at < timedelta(hours=72) and rl_phone in (None, phone_id):
                # A recent 136024 for this phone (or a legacy marker without
                # a phone id) means Meta's window is still saturated. With a
                # request log the cap check above expires precisely; without
                # one this is intentionally conservative — over-waiting costs
                # nothing, another burst costs the whole quota.
                window_end = rl_at + timedelta(hours=72)
                skip_reason = f"in 72h rate-limit window (until {window_end:%Y-%m-%d %H:%M}Z)"
            elif (
                meta.get("whatsapp_code_status") == "sent"
                and sent_at
                and sent_phone in (None, phone_id)
                and now - sent_at < timedelta(hours=24)
            ):
                # A code is already outstanding for this phone — the user has
                # 24h to enter it before we auto-resend. Without this gate the
                # 30-min beat would burst SMS codes until Meta 136024s again.
                resend_at = sent_at + timedelta(hours=24)
                skip_reason = (
                    f"code already sent {(now - sent_at).seconds // 3600}h ago "
                    f"— resend at {resend_at:%Y-%m-%d %H:%M}Z if still unverified"
                )

            if skip_reason:
                stats["rate_limited"] += 1
                stats["details"].append(f"{account.id}: {skip_reason} — skipping code request")
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
                continue

            # Try requesting a code — log the attempt up front: Meta counts
            # it toward the quota even when it rejects.
            times.append(now)
            req_log[phone_id] = [t.isoformat() for t in times]
            code_resp = await _request_code(phone_id, token, "SMS")
            status_code = code_resp.get("_status_code", 0)

            if status_code == 200:
                stats["code_sent"] += 1
                stats["details"].append(
                    f"{account.id}: ✅ verification code SENT to {status.get('display_phone_number', '?')}"
                )
                meta["whatsapp_code_requested_at"] = now.isoformat()
                meta["whatsapp_code_sent_at"] = now.isoformat()
                meta["whatsapp_code_sent_phone_id"] = phone_id
                meta["whatsapp_code_status"] = "sent"
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
                logger.info(
                    "WhatsApp verification code sent for account %s",
                    account.id,
                )
            else:
                error_code = code_resp.get("error", {}).get("code", 0)
                if error_code == 136024:
                    stats["rate_limited"] += 1
                    stats["details"].append(f"{account.id}: rate-limited (136024), waiting for 72h window reset")
                    meta["whatsapp_code_status"] = "rate_limited"
                    meta["whatsapp_rate_limited_at"] = now.isoformat()
                    meta["whatsapp_rate_limited_phone_id"] = phone_id
                    meta["whatsapp_code_error"] = code_resp.get("error", {}).get("error_user_msg", "")
                    account.meta_data = meta
                    flag_modified(account, "meta_data")
                    await db.commit()
                else:
                    stats["errors"] += 1
                    stats["details"].append(
                        f"{account.id}: request failed: {json.dumps(code_resp.get('error', {}))[:200]}"
                    )

    return stats


@celery_app.task(name="app.worker.tasks.whatsapp_verify.check_whatsapp_verification")
def check_whatsapp_verification() -> dict:
    """Check WhatsApp phone numbers and request verification codes.

    Runs every 30 minutes via Celery beat. No-op when rate-limited or
    already verified. When a code is successfully sent, the user must
    complete the verify + register steps manually.
    """
    logger.info("Running WhatsApp phone verification check...")
    try:
        stats = asyncio.run(_check_and_request())
        logger.info("WhatsApp verification check complete: %s", stats)
        return stats
    except Exception as e:
        logger.error("WhatsApp verification check failed: %s", e)
        return {"error": str(e)}
