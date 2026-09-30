from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.models.lead import Lead, LeadCompanySize, LeadInterest, LeadSource
from app.models.user import User
from app.services.leads import coerce_company_size, coerce_interest, create_lead
from app.services.playbook_email import deliver_playbook, playbook_url

router = APIRouter()


class LeadCreateRequest(BaseModel):
    source: LeadSource = Field(..., description="Inbound channel that captured the lead")
    name: str
    email: str
    company_size: str | None = Field(None, description="One of: solo, 2-5, 6-20, 21-50, 51-200, 200+")
    interest: str | None = Field(None, description="One of: cloud, growth, audit")
    notes: str | None = None
    social_account_id: uuid.UUID | None = None
    thread_id: str | None = None
    meta_data: dict | None = None


class LeadOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    source: LeadSource
    social_account_id: uuid.UUID | None
    thread_id: str | None
    name: str
    email: str
    company_size: LeadCompanySize | None
    interest: LeadInterest | None
    notes: str | None
    meta_data: dict
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[LeadOut])
async def list_leads(
    team_id: TeamId,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: LeadSource | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[LeadOut]:
    q = select(Lead).where(Lead.team_id == team_id)
    if source:
        q = q.where(Lead.source == source)
    q = q.order_by(desc(Lead.created_at)).limit(limit).offset(offset)
    leads = (await db.execute(q)).scalars().all()
    return [LeadOut.model_validate(lead, from_attributes=True) for lead in leads]


@router.post("", response_model=LeadOut)
async def create_lead_manual(
    body: LeadCreateRequest,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> LeadOut:
    company_size = coerce_company_size(body.company_size)
    if body.company_size and company_size is None:
        raise HTTPException(status_code=400, detail="Invalid company_size")

    interest = coerce_interest(body.interest)
    if body.interest and interest is None:
        raise HTTPException(status_code=400, detail="Invalid interest")

    lead = await create_lead(
        db,
        team_id=team_id,
        source=body.source,
        name=body.name,
        email=body.email,
        company_size=company_size,
        interest=interest,
        notes=body.notes,
        social_account_id=body.social_account_id,
        thread_id=body.thread_id,
        meta_data=body.meta_data or {},
    )
    return LeadOut.model_validate(lead, from_attributes=True)


# ── Public newsletter/lead capture (no auth — marketing site form) ────────

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class PublicLeadRequest(BaseModel):
    email: str = Field(..., max_length=320)
    name: str | None = Field(None, max_length=200)
    # Honeypot — rendered hidden in the form; bots fill it, humans don't.
    website: str | None = Field(None, max_length=200)


@router.post("/public")
@limiter.limit("10/minute")
async def create_lead_public(
    body: PublicLeadRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unauthenticated newsletter capture for the social.cloudless.gr funnel.

    Rate-limited + honeypot — no auth, so never accept anything richer than
    an email address. Leads land on the configured cloudless.gr team.

    ``playbook_delivery`` tells the form what actually happened:
      * ``email``        — playbook email queued for delivery
      * ``already_sent`` — this address already got it (no resend: stops the
                           form being used to spam a third party)
      * ``download``     — no email sender configured; offer the direct link
      * ``none``         — no playbook configured at all
    """
    settings = get_settings()
    url = playbook_url(settings)
    if body.website:
        # silent honeypot acceptance — same shape as a real success
        return {"ok": True, "playbook_delivery": "email" if url else "none", "playbook_url": url or None}
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    team_id_raw = settings.CLOUDLESS_WEB_ANALYTICS_TEAM_ID
    if not team_id_raw:
        raise HTTPException(status_code=503, detail="Lead capture not configured")
    team_id = uuid.UUID(team_id_raw)

    existing = (
        await db.execute(
            select(Lead).where(
                Lead.team_id == team_id,
                Lead.source == LeadSource.website,
                Lead.email == email,
            ).limit(1)
        )
    ).scalars().first()
    already_sent = bool(existing and (existing.meta_data or {}).get("playbook_email_queued_at"))

    lead = await create_lead(
        db,
        team_id=team_id,
        source=LeadSource.website,
        name=(body.name or email.split("@", 1)[0])[:200],
        email=email,
        interest=LeadInterest.cloud,
        notes="newsletter — Free Cloud Migration Playbook",
        meta_data={"form": "newsletter", "path": "public_landing"},
    )

    delivery = await deliver_playbook(db, lead, email, already_sent=already_sent, settings=settings)

    return {"ok": True, "playbook_delivery": delivery, "playbook_url": url or None}
