"""Public-lead nurture sequence — the follow-up the funnel was missing.

The public capture endpoints (/leads/public, /leads/public/audit) deliver
the playbook email and then go quiet. This service advances those leads
through a fixed 3-touch sequence stamped in lead.meta_data:

    day 0   — playbook delivery (existing, in deliver_playbook)
    day 3   — value email: what the playbook actually buys them
    day 7   — free audit CTA (or agency CTA if they already audited)
    day 14  — fixed-scope offer (€800 single fix / €3,600-mo full-stack bundle)

Deterministic, idempotent (meta_data stamps), team-scoped, unsubscribe
aware — a lead with meta_data.unsubscribed or nurture_opted_out never mails.

Quiet-lead lane (Pipeline Optimization Toolkit, Founders 3-11-2026): leads
that showed interest but never booked the free tech checkup get a faster
3-touch follow-up — gentle nudge → pain reminder → clean break — stamped
under ``meta_data.quiet_step`` with age measured from
``meta_data.quiet_lead_at`` (enrollment time, NOT created_at — an old lead
enrolled today starts at touch 1, not touch 3). Enroll via
``scripts/quiet_lead_followup.py``. A quiet-enrolled lead takes this lane
instead of the nurture ladder.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lead import Lead

logger = logging.getLogger(__name__)

NURTURE_FORMS = ("newsletter", "free-audit")

# Forms whose leads skip the ladder and enter the quiet lane directly —
# they already asked for the checkup (audit page / outreach intake), so
# the "didn't book" follow-up applies from lead creation, not after the
# playbook sequence.
QUIET_AUTO_FORMS = ("audit-request",)

STEPS: tuple[dict[str, Any], ...] = (
    {
        "day": 2,
        "key": "leak_lesson",
        "subject": "Is hidden software eating your profits?",
        "body": (
            "Hi {name},\n\n"
            "most startups and small businesses we work with are spending money\n"
            "on tech tools they don't even use. Worse, their teams lose hours\n"
            "every week copying data manually because their software tools do\n"
            "not talk to each other.\n\n"
            "At Cloudless, we fix that. We design easy websites, hosting, and\n"
            "automatic systems in just 14 days flat. You own everything we make,\n"
            "and there are no ongoing retainers.\n\n"
            "Let's plug your business cost leaks together this week. Book your\n"
            "free 30-minute tech checkup right here: https://cloudless.gr/en/consultation\n\n"
            "— Themis, Founder, Cloudless"
        ),
    },
    {
        "day": 4,
        "key": "fourteen_day_clock",
        "subject": "Fix your tech headaches in 2 weeks flat",
        "body": (
            "Hi {name},\n\n"
            "tech projects shouldn't take months, and they shouldn't cost a\n"
            "fortune in agency retainer fees. We launch clean websites and\n"
            "automated lead tracking systems in just 14 days flat. Our single\n"
            "fixes start at only €800, and you are never locked into a contract.\n\n"
            "If you are ready to speed up your daily tasks and cut down software\n"
            "bills, let's chat. Grab a free 30-minute slot this week before they\n"
            "fill up: https://cloudless.gr/en/consultation\n\n"
            "— Themis, Founder, Cloudless"
        ),
    },
    {
        "day": 14,
        "key": "offer",
        "subject": "If you want it done — fixed scope, 14 days",
        "body": (
            "Hi {name},\n\n"
            "if the audit or the playbook surfaced work you'd rather hand off,\n"
            "our engagements are fixed-scope: a single fix starts at €800,\n"
            "delivered inside 14 days, handoff documented. Bigger builds\n"
            "bundle at €3,600 a month.\n\n"
            "No pitch — just reply and I'll send over the audit + a scoped\n"
            "proposal if you want one.\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
)

# Quiet-lead 3-touch follow-up — for leads that expressed interest but never
# booked the free tech checkup. Copy adopted from the Pipeline Optimization
# Toolkit quiet-lead sequence (24h → 3d → 7d).
QUIET_STEPS: tuple[dict[str, Any], ...] = (
    {
        "day": 1,
        "key": "nudge",
        "subject": "Still up for your free tech checkup, {name}?",
        "body": (
            "Hi {name},\n\n"
            "just checking in — did you manage to grab a spot for your free\n"
            "tech checkup? Slots for this week are filling up fast, but you\n"
            "can see what is left right here: https://cloudless.gr/en/consultation\n\n"
            "Let me know if another time works better!\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
    {
        "day": 3,
        "key": "pain",
        "subject": "Found a couple of cost leaks on your site, {name}",
        "body": (
            "Hi {name},\n\n"
            "I was looking over your site again and found a couple of quick\n"
            "software cost leaks we can plug during our chat. If reducing\n"
            "clunky tasks or high bills is still a priority for {company},\n"
            "pick a time here: https://cloudless.gr/en/consultation\n\n"
            "No pitch, just a clear plan.\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
    {
        "day": 7,
        "key": "clean_break",
        "subject": "Closing the loop, {name}",
        "body": (
            "Hi {name},\n\n"
            "I assume this isn't a top priority for {company} right now, which\n"
            "is completely fine! I'll stop checking in so I don't crowd your\n"
            "inbox. If you ever want to fix your software bills or speed up\n"
            "tools down the road, you can always reach me at\n"
            "https://cloudless.gr\n\n"
            "Wish you the best!\n\n"
            "— Themistoklis, Cloudless"
        ),
    },
)

# ---------------------------------------------------------------------------
# DM script canon (Pipeline Optimization Toolkit / Agent Manifest, verbatim).
# Copy constants for the messaging lanes — LinkedIn DMs, Messenger, WhatsApp.
# The email implementation of the same cadence is QUIET_STEPS above; these are
# the DM-worded versions agents use verbatim (do not paraphrase).
# ---------------------------------------------------------------------------
DM_OPENERS: dict[str, str] = {
    "cold": (
        "Hey {name}! I came across {company} and noticed you guys are growing. "
        "If slow tools or high software bills are a headache for your team, we "
        "can help. We are doing free 30-minute tech checkups this week to find "
        "quick fixes for small businesses. No sales pitch, just a simple action "
        "plan. Are you free for a quick chat?"
    ),
    "warm": (
        "Hi {name}, I am Themis from Cloudless! Thanks for reading our weekly "
        "newsletter. Since you follow our tech updates, I wanted to reach out "
        "directly. If slow tools or high software bills are a headache for your "
        "team right now, we can help. We are giving free 30-minute tech checkups "
        "this week to find quick fixes. No sales pitch, just a simple plan. "
        "Are you free to chat?"
    ),
}

# Booking-link-requested follow-up: lead asked for the booking link in DM,
# then went quiet. Cadence 24h → 3d → 4d (from manifest FOLLOW_UP_AUTOMATION).
BOOKING_DM_STEPS: tuple[dict[str, Any], ...] = (
    {
        "after_hours": 24,
        "key": "nudge",
        "text": (
            "Hey {name}! Just checking in to see if you managed to grab a spot "
            "for your free tech checkup. Slots for this week are filling up "
            "fast, but you can see what is left right here: cloudless.gr. "
            "Let me know if another time works better!"
        ),
    },
    {
        "after_hours": 96,
        "key": "value",
        "text": (
            "Hey {name}! I was looking over your site again and found a couple "
            "of quick software cost leaks we can plug during our chat. If "
            "reducing clunky tasks or high bills is still a priority for your "
            "team, pick a time here: cloudless.gr. No pitch, just a clear plan."
        ),
    },
    {
        "after_hours": 192,
        "key": "clean_break",
        "text": (
            "Hey {name}, I assume this isn't a top priority for {company} "
            "right now, which is completely fine! I'll stop checking in so I "
            "don't crowd your inbox. If you ever want to fix your software "
            "bills or speed up tools down the road, you can always reach me "
            "at cloudless.gr. Wish you guys the best!"
        ),
    },
)

# Safety-valve: lead actively declines — one graceful close, then stop.
DM_SAFETY_VALVE: str = (
    "No problem at all! If your tech tools or software bills ever become a "
    "headache down the road, you can always find us at cloudless.gr. Have a "
    "great week with {company}!"
)



async def _cf_shadow_call(url_env: str, token_env: str, path: str, payload: dict) -> None:
    """Fire-and-forget CF worker call — never raises, skipped when env unset."""
    s = get_settings()
    base = (getattr(s, url_env, "") or "").strip()
    token = (getattr(s, token_env, "") or "").strip()
    if not base or not token:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(base.rstrip("/") + path, json=payload, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("cf shadow call %s failed: %s", path, exc)


def _now() -> datetime:
    return datetime.now(UTC)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    done = _int(meta.get("nurture_step"))
    for idx, step in enumerate(STEPS, start=1):
        if idx <= done:
            continue
        if age >= step["day"]:
            return {**step, "step": idx}
        return None  # not yet due — steps are sequential
    return None


def _quiet_age_days(lead: Lead) -> float:
    """Age since quiet-lane enrollment (meta_data.quiet_lead_at), not created_at."""
    meta = lead.meta_data or {}
    raw = meta.get("quiet_lead_at")
    if not raw:
        return 0.0
    try:
        started = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (_now() - started).total_seconds() / 86400


def _due_quiet_step(lead: Lead) -> dict[str, Any] | None:
    meta = lead.meta_data or {}
    if meta.get("unsubscribed") or meta.get("nurture_opted_out"):
        return None
    age = _quiet_age_days(lead)
    done = _int(meta.get("quiet_step"))
    for idx, step in enumerate(QUIET_STEPS, start=1):
        if idx <= done:
            continue
        if age >= step["day"]:
            return {**step, "step": idx}
        return None  # not yet due — steps are sequential
    return None


async def nurture_sweep(db: AsyncSession, team_id: uuid.UUID) -> dict[str, int]:
    """Advance all public-captured leads one step. Returns stats."""
    from app.services.email_digest import send_email

    stats = {"scanned": 0, "sent": 0, "skipped": 0, "rolled": 0}
    leads = (await db.execute(select(Lead).where(Lead.team_id == team_id, Lead.email.isnot(None)))).scalars().all()

    for lead in leads:
        meta = lead.meta_data or {}
        quiet = bool(meta.get("quiet_lead"))
        if not quiet and meta.get("form") in QUIET_AUTO_FORMS:
            if meta.get("unsubscribed") or meta.get("quiet_opted_out"):
                continue
            meta = {
                **meta,
                "quiet_lead": True,
                "quiet_lead_at": (lead.created_at or _now()).isoformat(),
            }
            lead.meta_data = meta
            quiet = True
            stats["rolled"] += 1
            await _cf_shadow_call(
                "LEAD_SCHEDULER_URL", "LEAD_SCHEDULER_TOKEN",
                f"/do/followup/{lead.id}/enroll", {"email": lead.email, "name": lead.name},
            )
        if not quiet and meta.get("form") not in NURTURE_FORMS:
            continue
        stats["scanned"] += 1
        step = _due_quiet_step(lead) if quiet else _due_step(lead)
        if not step:
            # Ladder finished → roll into the quiet-lead lane automatically:
            # the lead consumed the whole sequence without booking, so the
            # toolkit's 24h/3d/7d follow-up takes over from here.
            if not quiet and not meta.get("unsubscribed") and not meta.get("nurture_opted_out") and _int(meta.get("nurture_step")) >= len(STEPS):
                lead.meta_data = {
                    **meta,
                    "quiet_lead": True,
                    "quiet_lead_at": _now().isoformat(),
                }
                stats["rolled"] += 1
                await _cf_shadow_call(
                    "LEAD_SCHEDULER_URL", "LEAD_SCHEDULER_TOKEN",
                    f"/do/followup/{lead.id}/enroll", {"email": lead.email, "name": lead.name},
                )
            stats["skipped"] += 1
            continue
        name = (lead.name or "there").split()[0]
        company = meta.get("company") or "your team"
        try:
            await send_email(
                subject=step["subject"].format(name=name, company=company),
                text_body=step["body"].format(name=name, company=company),
                to_addrs=[lead.email],
            )
            stamp = "quiet_step" if quiet else "nurture_step"
            lead.meta_data = {
                **meta,
                stamp: step["step"],
                f"{stamp}_at": _now().isoformat(),
            }
            stats["sent"] += 1
        except Exception:  # noqa: BLE001 — one bad email mustn't stall the sweep
            stats["skipped"] += 1
    await db.commit()
    return stats


# ci: retrigger
