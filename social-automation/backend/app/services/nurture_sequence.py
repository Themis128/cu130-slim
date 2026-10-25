"""Public-lead nurture sequence — the follow-up the funnel was missing.

The public capture endpoints (/leads/public, /leads/public/audit) deliver
the playbook email and then go quiet. This service advances those leads
through a fixed 3-touch sequence stamped in lead.meta_data:

    day 0   — playbook delivery (existing, in deliver_playbook)
    day 3   — value email: what the playbook actually buys them
    day 7   — free audit CTA (or agency CTA if they already audited)
    day 14  — fixed-scope offer (the €800–2,400 14-day engagement)

Deterministic, idempotent (meta_data stamps), team-scoped, unsubscribe
aware — a lead with meta_data.unsubscribed or nurture_opted_out never mails.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead

NURTURE_FORMS = ("newsletter", "free-audit")

STEPS: tuple[dict[str, Any], ...] = (
    {
        "day": 3,
        "key": "value",
        "subject": "One thing worth doing with the playbook this week",
        "body": (
            "Hi {name},\n\n"
            "quick one — most people read the playbook and file it. The single\n"
            "highest-impact move is usually the first: pick ONE item from the\n"
            "checklist and ship it this week.\n\n"
            "If you want a second pair of eyes on which item to start with,\n"
            "reply to this email — it's free and takes 10 minutes.\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
    {
        "day": 7,
        "key": "audit_cta",
        "subject": "Want a free technical audit of your site?",
        "body": (
            "Hi {name},\n\n"
            "we run a free audit that scores your site on performance,\n"
            "security headers, and infra hygiene — real measurements, not a\n"
            "checklist. Takes under a minute and the findings are yours to\n"
            "keep either way.\n\n"
            "Grab yours: https://cloudless.gr/contact\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
    {
        "day": 14,
        "key": "offer",
        "subject": "If you want it done — fixed scope, 14 days",
        "body": (
            "Hi {name},\n\n"
            "if the audit or the playbook surfaced work you'd rather hand off,\n"
            "our engagements are fixed-scope: €800–2,400 per service,\n"
            "delivered inside 14 days, handoff documented.\n\n"
            "No pitch — just reply and I'll send over the audit + a scoped\n"
            "proposal if you want one.\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
)


def _now() -> datetime:
    return datetime.now(UTC)


def _lead_age_days(lead: Lead) -> float:
    created = lead.created_at
    if created is None:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (_now() - created).total_seconds() / 86400


def _due_step(lead: Lead) -> dict[str, Any] | None:
    meta = lead.meta_data or {}
    if meta.get("unsubscribed") or meta.get("nurture_opted_out"):
        return None
    age = _lead_age_days(lead)
    done = int(meta.get("nurture_step", 0) or 0)
    for idx, step in enumerate(STEPS, start=1):
        if idx <= done:
            continue
        if age >= step["day"]:
            return {**step, "step": idx}
        return None  # not yet due — steps are sequential
    return None


async def nurture_sweep(db: AsyncSession, team_id: uuid.UUID) -> dict[str, int]:
    """Advance all public-captured leads one step. Returns stats."""
    from app.services.email_digest import send_email

    stats = {"scanned": 0, "sent": 0, "skipped": 0}
    leads = (await db.execute(
        select(Lead).where(Lead.team_id == team_id, Lead.email.isnot(None))
    )).scalars().all()

    for lead in leads:
        meta = lead.meta_data or {}
        if meta.get("form") not in NURTURE_FORMS:
            continue
        stats["scanned"] += 1
        step = _due_step(lead)
        if not step:
            stats["skipped"] += 1
            continue
        name = (lead.name or "there").split()[0]
        try:
            await send_email(
                subject=step["subject"],
                text_body=step["body"].format(name=name),
                to_addrs=[lead.email],
            )
            lead.meta_data = {
                **meta,
                "nurture_step": step["step"],
                "nurture_step_at": _now().isoformat(),
            }
            stats["sent"] += 1
        except Exception:  # noqa: BLE001 — one bad email mustn't stall the sweep
            stats["skipped"] += 1
    await db.commit()
    return stats
