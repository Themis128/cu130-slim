"""Celery task — WhatsApp message-template status poller.

Meta pushes ``message_template_status_update`` webhook events, but webhook
delivery is best-effort; this beat task polls ``GET /{waba}/message_templates``
every 15 minutes as the authoritative fallback and alerts on any status
transition (PENDING → APPROVED/REJECTED/PAUSED/DISABLED).

State is kept in ``social_accounts.meta_data.whatsapp_templates`` as
``{name|language: status}`` so only *changes* alert — first sight of a
template records silently. Rejected templates carry ``rejected_reason``
through to Slack so the fix is actionable.

No-op when the account has no ``waba_id`` or the token can't read templates.
"""

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.security import decrypt_token
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v26.0"
GRAPH_BASE = "https://graph.facebook.com"


async def _fetch_templates(waba_id: str, token: str) -> list[dict] | None:
    """Return the WABA's message templates, or None on API failure."""
    templates: list[dict] = []
    url = f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{waba_id}/message_templates"
    params: dict[str, str] = {
        "fields": "name,status,language,category,rejected_reason",
        "limit": "100",
        "access_token": token,
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        while url:
            r = await http.get(url, params=params)
            if r.status_code != 200:
                logger.warning("template poll failed for WABA %s: %s", waba_id, r.text[:300])
                return None
            data = r.json()
            templates.extend(data.get("data", []))
            url = (data.get("paging") or {}).get("next") or ""
            params = {}
    return templates


async def _poll() -> dict[str, Any]:
    stats: dict[str, Any] = {"accounts": 0, "templates": 0, "changes": 0, "errors": 0, "details": []}
    alerts: list[str] = []

    async with task_session() as db:
        from app.models.social_account import SocialAccount

        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == "whatsapp"))
        accounts = result.scalars().all()

        for account in accounts:
            stats["accounts"] += 1
            meta = account.meta_data or {}
            if isinstance(meta, str):
                meta = json.loads(meta)

            waba_id = meta.get("waba_id") or meta.get("business_account_id")
            if not waba_id:
                continue
            token = decrypt_token(account.access_token_enc)
            if not token:
                stats["errors"] += 1
                continue

            templates = await _fetch_templates(waba_id, token)
            if templates is None:
                stats["errors"] += 1
                continue
            stats["templates"] += len(templates)

            known: dict[str, str] = dict(meta.get("whatsapp_templates") or {})
            current: dict[str, str] = {}
            dirty = False
            for t in templates:
                key = f"{t.get('name')}|{t.get('language')}"
                status = t.get("status") or "UNKNOWN"
                current[key] = status
                prev = known.get(key)
                if prev is None:
                    # First sight — record silently (avoids alert storms on
                    # first run after deploy).
                    dirty = True
                    continue
                if prev != status:
                    stats["changes"] += 1
                    reason = t.get("rejected_reason") or ""
                    alerts.append(
                        f"WhatsApp template `{t.get('name')}` ({t.get('language')}) "
                        f"{prev} → {status}" + (f" — {reason}" if reason else "")
                    )
                    dirty = True
            # Templates that disappeared from the WABA are worth a note too.
            for key in known:
                if key not in current:
                    stats["changes"] += 1
                    alerts.append(f"WhatsApp template `{key}` removed from WABA")
                    dirty = True

            if dirty:
                meta["whatsapp_templates"] = current
                account.meta_data = meta
                flag_modified(account, "meta_data")
                await db.commit()
                stats["details"].append(f"{account.id}: {len(current)} templates tracked")

    if alerts:
        try:
            from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

            await post_alert_to_slack(":whatsapp: *Template status changes*\n" + "\n".join(f"• {a}" for a in alerts))
        except Exception:  # noqa: BLE001
            logger.exception("template status slack post failed")

    return stats


@celery_app.task(name="app.worker.tasks.whatsapp_templates.poll_whatsapp_templates")
def poll_whatsapp_templates() -> dict:
    """Poll WABA template statuses and alert on transitions. Runs every 15 min."""
    try:
        stats = run_async(_poll())
        logger.info("whatsapp template poll: %s", stats)
        return stats
    except Exception as e:  # noqa: BLE001
        logger.error("whatsapp template poll failed: %s", e)
        return {"error": str(e)}
