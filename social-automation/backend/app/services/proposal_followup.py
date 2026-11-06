"""Proposal follow-up ladder — sent proposals don't go cold.

``send_proposal`` mails the lead once and stops. This sweep advances
``status=sent`` proposals through a fixed 3-touch sequence stamped in
``proposal.meta_data["followup_step"]``:

    day 3   — bump: short nudge under the original subject
    day 7   — last-call: the fixed scope has a shelf life
    day 14  — break-up: polite close-out, door stays open

Idempotent by construction (``meta_data`` stamps + ``followed_out`` flag),
reply-aware — a proposal whose ``meta_data["replied"]`` is set by the
crm-engagement reply matcher, or that left ``sent`` status, never mails
again. Templates stay in the same plain Themis voice as the proposal mail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agency import AgencyProposal, AgencyProposalStatus
from app.services import espocrm_client as espo

logger = logging.getLogger(__name__)

REPLY_TO = "tbaltzakis@cloudless.gr"
FROM_NAME = "Themistoklis — Cloudless"
AUDIT_CTA_URL = "https://cloudless.gr/en/consultation"


def _factory(custom=None):
    if custom is not None:
        return custom
    from app.worker._async import get_session_factory

    return get_session_factory()


async def _default_team_id(db: AsyncSession) -> uuid.UUID | None:
    from app.models.user import Team

    return (await db.execute(select(Team.id).order_by(Team.created_at.asc()).limit(1))).scalars().first()


def _steps(proposal: AgencyProposal) -> tuple[dict[str, Any], ...]:
    """Concrete follow-up copy for this proposal (service/price personalized)."""
    name = (proposal.recipient_name or "").strip() or "there"
    price = (
        f"€{proposal.price_low_eur}–€{proposal.price_high_eur}"
        if proposal.price_low_eur and proposal.price_high_eur
        else "the quoted fixed price"
    )
    return (
        {
            "day": 3,
            "key": "bump",
            "subject": f"Re: {proposal.subject}",
            "body": (
                f"Hi {name},\n\n"
                "quick nudge on the audit + fixed-scope plan I sent over —\n"
                "happy to answer questions or walk through it on a short call.\n\n"
                f"Grab a free 30-minute slot here: {AUDIT_CTA_URL}\n\n"
                "— Themistoklis, Cloudless"
            ),
        },
        {
            "day": 7,
            "key": "last_call",
            "subject": f"Re: {proposal.subject}",
            "body": (
                f"Hi {name},\n\n"
                f"the {price} scope holds for two weeks from when I sent it —\n"
                "after that I'll re-quote against whatever changed.\n\n"
                "If now isn't the right time, a one-line 'not now' is fine too;\n"
                "I'll close the file and won't keep mailing you.\n\n"
                "— Themistoklis, Cloudless"
            ),
        },
        {
            "day": 14,
            "key": "breakup",
            "subject": f"Re: {proposal.subject}",
            "body": (
                f"Hi {name},\n\n"
                "last note from me — I'm closing the file on the audit and\n"
                "proposal so my pipeline stays honest.\n\n"
                "The findings stay valid for a while; if you want the fix later,\n"
                "just reply and I'll re-quote. No follow-ups after this.\n\n"
                "— Themistoklis, Cloudless"
            ),
        },
    )


async def proposal_followup_sweep(*, session_factory=None) -> dict[str, Any]:
    """Advance sent proposals through the day-3/7/14 follow-up ladder."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    now = datetime.now(UTC)
    stats: dict[str, Any] = {"status": "ok", "sent": 0, "closed_out": 0, "skipped": 0}
    sent_lines: list[str] = []
    async with factory() as db:
        team_id = await _default_team_id(db)
        if not team_id:
            return {"status": "skipped", "reason": "no_team"}
        proposals = (
            (
                await db.execute(
                    select(AgencyProposal)
                    .where(
                        AgencyProposal.team_id == team_id,
                        AgencyProposal.status == AgencyProposalStatus.sent,
                        AgencyProposal.sent_at.is_not(None),
                        AgencyProposal.recipient_email.is_not(None),
                    )
                    .order_by(AgencyProposal.sent_at.asc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        for proposal in proposals:
            md = dict(proposal.meta_data or {})
            if md.get("followed_out") or md.get("replied"):
                stats["skipped"] += 1
                continue
            sent_at = proposal.sent_at
            if sent_at is None or proposal.recipient_email is None:
                stats["skipped"] += 1
                continue
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=UTC)
            age_days = (now - sent_at).days
            if age_days < 0:
                stats["skipped"] += 1
                continue
            done_steps = {s["key"] for s in md.get("followup_history", [])}
            steps = _steps(proposal)
            due = next((s for s in steps if age_days >= int(s["day"]) and s["key"] not in done_steps), None)
            if due is None:
                if age_days > int(steps[-1]["day"]) and not md.get("followed_out"):
                    md["followed_out"] = True
                    proposal.meta_data = md
                    stats["closed_out"] += 1
                continue

            from app.services.email_digest import send_email

            await send_email(
                subject=due["subject"],
                text_body=due["body"],
                to_addrs=[proposal.recipient_email],
                reply_to=REPLY_TO,
                from_name=FROM_NAME,
            )
            history = list(md.get("followup_history", []))
            history.append({"key": due["key"], "day": int(due["day"]), "sent_at": now.isoformat()})
            md["followup_history"] = history
            md["followup_step"] = due["key"]
            if due["key"] == steps[-1]["key"]:
                md["followed_out"] = True
                stats["closed_out"] += 1
            proposal.meta_data = md
            stats["sent"] += 1
            sent_lines.append(f"{proposal.recipient_email}: {due['key']} (day {due['day']})")

            if proposal.espo_lead_id:
                await espo.add_lead_note(
                    proposal.espo_lead_id,
                    f"Proposal follow-up sent: {due['key']} (day {due['day']} since proposal)",
                )
        await db.commit()

    if sent_lines or stats["closed_out"]:
        try:
            from app.services.slack_notifications import post_digest_text_to_slack

            lines = [f"*Proposal follow-up sweep* — {stats['sent']} sent, {stats['closed_out']} closed out"]
            lines += [f"• {line}" for line in sent_lines[:5]]
            await post_digest_text_to_slack("\n".join(lines))
        except Exception:  # noqa: BLE001
            logger.exception("proposal followup slack report failed")
    logger.info("agency proposal followup sweep: %s", stats)
    return stats
