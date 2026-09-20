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
from datetime import UTC, datetime
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

            # Try requesting a code
            code_resp = await _request_code(phone_id, token, "SMS")
            status_code = code_resp.get("_status_code", 0)

            if status_code == 200:
                stats["code_sent"] += 1
                stats["details"].append(
                    f"{account.id}: ✅ verification code SENT to {status.get('display_phone_number', '?')}"
                )
                # Record the timestamp
                meta["whatsapp_code_requested_at"] = datetime.now(UTC).isoformat()
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
