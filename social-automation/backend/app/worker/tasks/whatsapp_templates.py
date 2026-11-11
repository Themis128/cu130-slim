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
from datetime import UTC, datetime, timedelta
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
# Meta documents template review at up to 24-48h; past that the fix is a
# support ticket, so alert once per template instead of polling forever.
PENDING_STALE_AFTER = timedelta(hours=48)


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
            pending_since: dict[str, str] = dict(
                meta.get("whatsapp_template_pending_since") or {}
            )
            pending_since_orig = dict(pending_since)
            stale_alerted: set[str] = set(
                meta.get("whatsapp_template_stale_alerted") or []
            )
            stale_alerted_orig = set(stale_alerted)
            now = datetime.now(UTC)
            current: dict[str, str] = {}
            dirty = False
            for t in templates:
                key = f"{t.get('name')}|{t.get('language')}"
                status = t.get("status") or "UNKNOWN"
                current[key] = status
                prev = known.get(key)
                if status == "PENDING":
                    pending_since.setdefault(key, now.isoformat())
                else:
                    pending_since.pop(key, None)
                    stale_alerted.discard(key)
                if prev is None:
                    # First sight — record silently (avoids alert storms on
                    # first run after deploy).
                    dirty = True
                    continue
                if prev != status:
                    stats["changes"] += 1
                    reason = t.get("rejected_reason") or ""
                    alerts.append(f"WhatsApp template `{t.get('name')}` ({t.get('language')}) {prev} → {status}" + (f" — {reason}" if reason else ""))
                    dirty = True
            # Templates that disappeared from the WABA are worth a note too.
            for key in known:
                if key not in current:
                    stats["changes"] += 1
                    alerts.append(f"WhatsApp template `{key}` removed from WABA")
                    dirty = True

            # Stale-PENDING escalation: Meta's documented review window is
            # ~24-48h; beyond that the remediation is a support ticket.
            for key, since_iso in pending_since.items():
                if key in stale_alerted or key not in current:
                    continue
                try:
                    since = datetime.fromisoformat(since_iso)
                except ValueError:
                    pending_since[key] = now.isoformat()
                    dirty = True
                    continue
                if now - since > PENDING_STALE_AFTER:
                    name, _, lang = key.partition("|")
                    alerts.append(
                        f"WhatsApp template `{name}` ({lang}) still PENDING "
                        f">48h — Meta review window exceeded; open a "
                        f"support ticket in Business Support Home"
                    )
                    stale_alerted.add(key)
                    dirty = True

            # Drop tracking for templates no longer on the WABA.
            pending_since = {k: v for k, v in pending_since.items() if k in current}

            if dirty or pending_since != pending_since_orig or stale_alerted != stale_alerted_orig:
                meta["whatsapp_templates"] = current
                meta["whatsapp_template_pending_since"] = pending_since
                meta["whatsapp_template_stale_alerted"] = sorted(stale_alerted)
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
