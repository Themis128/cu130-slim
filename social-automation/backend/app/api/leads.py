from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

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
    # Optional attribution sent by the cloudless.gr server-side forwarder
    # (/api/playbook-lead, through Cloudflare Access with a service token).
    # All bounded + sanitised in ``public_lead_meta``; absent for the
    # in-app LeadCapture form, which keeps its historical meta_data shape.
    site: str | None = Field(None, max_length=64)
    form: str | None = Field(None, max_length=40)
    page_path: str | None = Field(None, max_length=200)
    locale: str | None = Field(None, max_length=10)
    consent: bool | None = None
    consent_at: str | None = Field(None, max_length=40)


_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9/._~%-]{0,199}$")


def _iso_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def public_lead_meta(body: PublicLeadRequest) -> dict:
    """meta_data for a public lead: legacy defaults + validated attribution."""
    meta: dict = {"form": "newsletter", "path": "public_landing"}
    if body.site and _TOKEN_RE.match(body.site):
        meta["site"] = body.site
    if body.form and _TOKEN_RE.match(body.form):
        meta["form"] = body.form
    if body.page_path and _PATH_RE.match(body.page_path):
        meta["path"] = body.page_path
    if body.locale and _LOCALE_RE.match(body.locale):
        meta["locale"] = body.locale
    if body.consent is True:
        meta["consent"] = True
        meta["consent_at"] = _iso_or_none(body.consent_at) or datetime.now(UTC).isoformat()
    return meta


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
    if body.consent is False:
        # A forwarder that explicitly reports "no consent" must not trigger mail.
        raise HTTPException(status_code=400, detail="Consent required")
    team_id_raw = settings.CLOUDLESS_WEB_ANALYTICS_TEAM_ID
    if not team_id_raw:
        raise HTTPException(status_code=503, detail="Lead capture not configured")
    team_id = uuid.UUID(team_id_raw)
    meta = public_lead_meta(body)

    # Any website lead row for this address already stamped? (rows aren't
    # unique per email). The atomic Redis claim in deliver_playbook is what
    # actually guarantees one email per address under concurrency.
    prior = (
        await db.execute(
            select(Lead.meta_data).where(
                Lead.team_id == team_id,
                Lead.source == LeadSource.website,
                Lead.email == email,
            )
        )
    ).scalars().all()
    already_sent = any((md or {}).get("playbook_email_queued_at") for md in prior)

    lead = await create_lead(
        db,
        team_id=team_id,
        source=LeadSource.website,
        name=(body.name or email.split("@", 1)[0])[:200],
        email=email,
        interest=LeadInterest.cloud,
        notes=f"{meta['form']} — Free Cloud Migration Playbook"
        + (f" (via {meta['site']})" if meta.get("site") else ""),
        meta_data=meta,
    )

    delivery = await deliver_playbook(db, lead, email, already_sent=already_sent, settings=settings)

    return {"ok": True, "playbook_delivery": delivery, "playbook_url": url or None}
