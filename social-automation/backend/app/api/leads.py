from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.db.session import get_db
from app.models.lead import Lead, LeadCompanySize, LeadInterest, LeadSource
from app.models.user import User
from app.services.leads import coerce_company_size, coerce_interest, create_lead

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
    return [LeadOut.model_validate(l, from_attributes=True) for l in leads]


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

