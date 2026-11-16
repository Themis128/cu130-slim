"""LinkedIn closed-loop conversions — EspoCRM won leads → Conversions API.

The forward loop (LinkedIn lead → EspoCRM → ``lead_created`` event) already
exists. This task closes it: EspoCRM leads that reached ``Converted``
status get a downstream ``lead_won`` conversion streamed back to LinkedIn
with ``conversionValue`` when an amount is known — LinkedIn then optimizes
delivery toward leads that actually close, not just form fills.

Dedup ledger: ``analytics_events`` rows with
``event_type="linkedin_offline_conversion"`` and
``platform_event_id="{espo_lead_id}:lead_won"`` — each EspoCRM lead fires
at most once. Runs daily; EspoCRM is the source of truth for stage.
"""

import hashlib
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.models.analytics import AnalyticsEvent
from app.worker._async import run_async, task_session

logger = logging.getLogger(__name__)

ESPO_STATUSES = os.getenv("LINKEDIN_OFFLINE_CONVERSION_STATUSES", "Converted")
MAX_LEADS = int(os.getenv("LINKEDIN_OFFLINE_CONVERSION_MAX", "200"))


@shared_task(
    name="app.worker.tasks.linkedin_offline_conversions.sync_linkedin_offline_conversions",
    bind=True,
    max_retries=0,
)
def sync_linkedin_offline_conversions(self):
    return run_async(_run())


def _sha256(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _lead_response_ref(description: str) -> str | None:
    """Extract the LinkedIn response ref stored by route_lead_response."""
    m = re.search(r"lead-gen response\s+(\S+)", description or "")
    return m.group(1) if m else None


async def _already_sent(db, team_id, dedup_key: str) -> bool:
    """True only when a previous attempt was ACCEPTED by LinkedIn.

    Failed/skipped attempts also write ledger rows (for audit), so dedup must
    inspect the stored result — otherwise one HTTP 503 permanently blocks the
    lead from ever being retried.
    """
    rows = (
        (
            await db.execute(
                select(AnalyticsEvent.meta_data)
                .where(
                    AnalyticsEvent.event_type == "linkedin_offline_conversion",
                    AnalyticsEvent.platform_event_id == dedup_key,
                    AnalyticsEvent.team_id == team_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for md in rows:
        result = (md or {}).get("result") or {}
        if result.get("ok") or result.get("status") in (200, 201):
            return True
    return False


async def _espo_won_leads() -> list[dict[str, Any]]:
    from app.services import espocrm_client

    statuses = [s.strip() for s in ESPO_STATUSES.split(",") if s.strip()]
    where = [
        {"type": "in", "attribute": "status", "value": statuses},
        {"type": "equals", "attribute": "source", "value": "Social Media"},
    ]
    return await espocrm_client.list_all(
        "Lead",
        select=["id", "emailAddress", "status", "description", "amount", "amountCurrency", "modifiedAt"],
        where=where,
        order_by="modifiedAt",
        max_items=MAX_LEADS,
    )


async def _run() -> dict[str, Any]:
    from app.models.social_account import SocialAccount
    from app.models.user import Team
    from app.services import linkedin_advanced
    from app.services.slack_notifications import post_alert_to_slack, send_telegram_alert

    if not linkedin_advanced.ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}

    try:
        leads = await _espo_won_leads()
    except Exception as exc:  # noqa: BLE001 — EspoCRM outage must not kill the beat
        logger.warning("offline-conversions: EspoCRM query failed: %s", exc)
        return {"error": str(exc)[:200]}

    rule = await linkedin_advanced.resolve_conversion_rule("lead_won")
    if not rule:
        return {"skip": "no lead_won conversion rule (ensure task creates it daily)"}

    stats: dict[str, Any] = {"won_leads": len(leads), "sent": 0, "dedup": 0, "no_email": 0, "errors": []}
    async with task_session() as db:
        team = (await db.execute(select(Team).limit(1))).scalars().first()
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        if not team or not acc:
            return {"error": "no-team-or-linkedin-account"}

        for lead in leads:
            espo_id = str(lead.get("id") or "")
            email = (lead.get("emailAddress") or "").strip()
            if not espo_id or not email:
                stats["no_email"] += 1
                continue
            dedup_key = f"{espo_id}:lead_won"
            if await _already_sent(db, team.id, dedup_key):
                stats["dedup"] += 1
                continue
            desc = str(lead.get("description") or "")
            if "LinkedIn" not in desc:
                stats["dedup"] += 1  # not a LinkedIn-sourced lead — skip silently
                continue
            amount = lead.get("amount")
            conv = await linkedin_advanced.stream_conversion(
                conversion_rule=rule,
                happened_at_ms=int(datetime.now(UTC).timestamp() * 1000),
                email_sha256=_sha256(email),
                lead_id=_lead_response_ref(desc),
                conversion_value=(
                    float(amount)
                    if isinstance(amount, int | float)
                    or (isinstance(amount, str) and amount.replace(".", "", 1).isdigit())
                    else None
                ),
                currency_code=lead.get("amountCurrency") or "EUR",
            )
            ok = conv.get("ok") or conv.get("status") in (200, 201)
            db.add(
                AnalyticsEvent(
                    team_id=team.id,
                    social_account_id=acc.id,
                    event_type="linkedin_offline_conversion",
                    platform="linkedin",
                    platform_event_id=dedup_key,
                    occurred_at=datetime.now(UTC),
                    meta_data={
                        "espo_lead_id": espo_id,
                        "email_sha256": _sha256(email)[:16],
                        "amount": amount,
                        "result": {k: conv.get(k) for k in ("status", "ok", "error")},
                    },
                )
            )
            if ok:
                stats["sent"] += 1
            else:
                stats["errors"].append({"espo_id": espo_id, "status": conv.get("status"), "error": conv.get("error")})
        await db.commit()

    if stats["sent"]:
        await post_alert_to_slack(f":moneybag: LinkedIn closed-loop: {stats['sent']} won lead(s) reported as conversions")
        await send_telegram_alert("ops", f"LinkedIn closed-loop conversions: {stats['sent']} won EspoCRM lead(s) → lead_won events")
    if stats["errors"]:
        logger.warning("offline-conversions errors: %s", stats["errors"][:5])
    return stats
