from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lead import Lead, LeadCompanySize, LeadInterest, LeadSource

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(_norm_email(email)))


def coerce_company_size(value: str | None) -> LeadCompanySize | None:
    if not value:
        return None
    v = value.strip()
    for item in LeadCompanySize:
        if v == item.value:
            return item
    return None


def coerce_interest(value: str | None) -> LeadInterest | None:
    if not value:
        return None
    v = value.strip().lower()
    for item in LeadInterest:
        if v == item.value:
            return item
    return None


async def create_lead(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    source: LeadSource,
    name: str,
    email: str,
    company_size: LeadCompanySize | None = None,
    interest: LeadInterest | None = None,
    notes: str | None = None,
    social_account_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    meta_data: dict[str, Any] | None = None,
    dedupe_on_email: bool = True,
) -> Lead:
    """Persist a lead and optionally notify external automation (best-effort).

    `dedupe_on_email` is deliberately conservative: within a team + source, keep
    only one row per email to avoid spammy duplicates from retries/webhooks.
    """
    email_n = _norm_email(email)
    if not _is_valid_email(email_n):
        raise ValueError("Invalid email")
    name_n = (name or "").strip()
    if not name_n:
        raise ValueError("Name is required")

    if dedupe_on_email:
        existing = (
            await db.execute(
                select(Lead).where(
                    Lead.team_id == team_id,
                    Lead.source == source,
                    Lead.email == email_n,
                ).limit(1)
            )
        ).scalars().first()
        if existing:
            # Best-effort: enrich missing fields
            changed = False
            if not existing.name and name_n:
                existing.name = name_n
                changed = True
            if not existing.company_size and company_size:
                existing.company_size = company_size
                changed = True
            if not existing.interest and interest:
                existing.interest = interest
                changed = True
            if not existing.notes and notes:
                existing.notes = notes
                changed = True
            if social_account_id and not existing.social_account_id:
                existing.social_account_id = social_account_id
                changed = True
            if thread_id and not existing.thread_id:
                existing.thread_id = thread_id
                changed = True
            if meta_data:
                md = dict(existing.meta_data or {})
                md.update(meta_data)
                existing.meta_data = md
                changed = True
            if changed:
                existing.updated_at = datetime.now(UTC)
                await db.commit()
            return existing

    lead = Lead(
        id=uuid.uuid4(),
        team_id=team_id,
        source=source,
        social_account_id=social_account_id,
        thread_id=thread_id,
        name=name_n,
        email=email_n,
        company_size=company_size,
        interest=interest,
        notes=notes,
        meta_data=meta_data or {},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(lead)
    await db.commit()

    # Non-blocking integrations.
    try:
        await notify_lead_created(lead)
    except Exception:
        logger.debug("Lead notifications failed (non-fatal)", exc_info=True)

    return lead


async def notify_lead_created(lead: Lead) -> None:
    settings = get_settings()

    # 1) Slack (optional) — prefer dedicated lead webhook, fallback to digest webhook.
    slack_webhook = (getattr(settings, "SLACK_LEADS_WEBHOOK_URL", "") or "").strip() or (settings.SLACK_WEBHOOK_URL or "").strip()
    slack_token = (settings.SLACK_BOT_TOKEN or "").strip() or (settings.SLACK_ACCESS_TOKEN or "").strip()
    slack_channel = (getattr(settings, "SLACK_LEADS_CHANNEL_ID", "") or "").strip() or (settings.SLACK_CHANNEL_ID or "").strip()

    if slack_webhook or slack_token:
        try:
            from app.services.slack_notifications import _post_slack_text

            interest = lead.interest.value if lead.interest else "—"
            size = lead.company_size.value if lead.company_size else "—"
            text = (
                "*New lead captured*\n"
                f"- Source: `{lead.source.value}`\n"
                f"- Name: {lead.name}\n"
                f"- Email: {lead.email}\n"
                f"- Company size: `{size}`\n"
                f"- Interest: `{interest}`\n"
            )
            if lead.notes:
                text += f"- Notes: {lead.notes[:500]}\n"

            await _post_slack_text(
                text=text,
                webhook_url=slack_webhook,
                token=slack_token,
                channel_id=slack_channel,
                purpose="leads",
            )
        except Exception:
            logger.debug("Slack lead notify failed (non-fatal)", exc_info=True)

    # 2) Generic webhook (optional) — ideal for n8n workflows.
    webhook_url = (getattr(settings, "LEAD_CREATED_WEBHOOK_URL", "") or "").strip()
    if webhook_url:
        payload = {
            "event": "lead.created",
            "lead": {
                "id": str(lead.id),
                "team_id": str(lead.team_id),
                "source": lead.source.value,
                "social_account_id": str(lead.social_account_id) if lead.social_account_id else None,
                "thread_id": lead.thread_id,
                "name": lead.name,
                "email": lead.email,
                "company_size": lead.company_size.value if lead.company_size else None,
                "interest": lead.interest.value if lead.interest else None,
                "notes": lead.notes,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(webhook_url, json=payload)
        except Exception:
            logger.debug("Lead webhook notify failed (non-fatal)", exc_info=True)

