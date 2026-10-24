"""Autonomous-agency API — audits, proposals, 14-day projects.

Operator-only surface (``require_admin``): list/run site audits, review and
approve proposals (the human gate before any client email), and kick off
the 14-day project loop. Slack approve/reject buttons proxy here via the
cloudless.gr ``/api/slack/interactions`` bridge.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.agency import (
    AgencyAudit,
    AgencyAuditStatus,
    AgencyProject,
    AgencyProjectStatus,
    AgencyProposal,
    AgencyProposalStatus,
)
from app.models.user import TeamMember, User
from app.services.agency_catalog import DELIVERY_DAYS, SERVICES, price_range
from app.services.agency_pipeline import (
    project_milestones,
    render_proposal,
    send_proposal,
)
from app.services.site_audit import run_site_audit
from app.services.url_safety import UnsafeUrlError

router = APIRouter()


async def _team_id(db: AsyncSession, user: User) -> uuid.UUID:
    result = await db.execute(
        select(TeamMember.team_id).where(TeamMember.user_id == user.id).limit(1)
    )
    team_id = result.scalars().first()
    if not team_id:
        raise HTTPException(status_code=404, detail="Team not found")
    return team_id


async def _team_audit(db: AsyncSession, team_id: uuid.UUID, audit_id: uuid.UUID) -> AgencyAudit:
    row = (
        await db.execute(
            select(AgencyAudit).where(AgencyAudit.id == audit_id, AgencyAudit.team_id == team_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return row


async def _team_proposal(db: AsyncSession, team_id: uuid.UUID, proposal_id: uuid.UUID) -> AgencyProposal:
    row = (
        await db.execute(
            select(AgencyProposal).where(
                AgencyProposal.id == proposal_id, AgencyProposal.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row


async def _team_project(db: AsyncSession, team_id: uuid.UUID, project_id: uuid.UUID) -> AgencyProject:
    row = (
        await db.execute(
            select(AgencyProject).where(
                AgencyProject.id == project_id, AgencyProject.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return row


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AuditOut(BaseModel):
    id: uuid.UUID
    url: str
    status: AgencyAuditStatus
    score: int | None
    findings: list
    recommended_services: list
    action_plan: str | None
    error: str | None
    lead_id: uuid.UUID | None
    espo_lead_id: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AuditRunRequest(BaseModel):
    url: str
    lead_id: uuid.UUID | None = None
    espo_lead_id: str | None = Field(None, max_length=40)


class ProposalOut(BaseModel):
    id: uuid.UUID
    audit_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    espo_lead_id: str | None
    recipient_email: str | None
    recipient_name: str | None
    services: list
    price_low_eur: int | None
    price_high_eur: int | None
    subject: str
    status: AgencyProposalStatus
    sent_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProposalDetailOut(ProposalOut):
    body_md: str


class ProjectOut(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID | None
    name: str
    client_email: str | None
    kickoff_at: datetime
    due_at: datetime
    status: AgencyProjectStatus
    milestones: list
    last_client_update_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectDetailOut(ProjectOut):
    handoff_md: str | None
    meta_data: dict


class ProjectCreateRequest(BaseModel):
    proposal_id: uuid.UUID | None = None
    name: str = Field(..., min_length=1, max_length=300)
    client_email: str | None = None
    kickoff_at: datetime | None = None


# ---------------------------------------------------------------------------
# Audits (the Architect)
# ---------------------------------------------------------------------------


@router.get("/audits", response_model=list[AuditOut])
async def list_audits(
    status: AgencyAuditStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _team_id(db, current_user)
    q = select(AgencyAudit).where(AgencyAudit.team_id == team_id)
    if status:
        q = q.where(AgencyAudit.status == status)
    rows = (await db.execute(q.order_by(desc(AgencyAudit.created_at)).limit(limit))).scalars().all()
    return rows


@router.get("/audits/{audit_id}", response_model=AuditOut)
async def get_audit(
    audit_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _team_audit(db, await _team_id(db, current_user), audit_id)


@router.post("/audits", response_model=AuditOut, status_code=201)
async def run_audit_now(
    body: AuditRunRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manual Architect run — audit a URL synchronously and store the result."""
    team_id = await _team_id(db, current_user)
    audit = AgencyAudit(
        team_id=team_id, lead_id=body.lead_id, espo_lead_id=body.espo_lead_id,
        url=body.url, status=AgencyAuditStatus.running,
    )
    db.add(audit)
    await db.flush()
    try:
        result = await run_site_audit(body.url)
    except UnsafeUrlError as exc:
        audit.status = AgencyAuditStatus.failed
        audit.error = f"unsafe_url: {exc}"
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        audit.status = AgencyAuditStatus.failed
        audit.error = str(exc)[:1000]
        await db.commit()
        raise HTTPException(status_code=502, detail=f"audit failed: {exc}") from exc

    audit.status = AgencyAuditStatus.completed
    audit.score = result["score"]
    audit.findings = result["findings"]
    audit.recommended_services = result["recommended_services"]
    audit.action_plan = result["action_plan"]
    audit.completed_at = datetime.now(UTC)
    audit.meta_data = {
        "ttfb_ms": result["ttfb_ms"],
        "http_status": result["http_status"],
        "tech": result["tech"],
        "analytics": result["analytics"],
        "manual": True,
    }
    await db.commit()
    await db.refresh(audit)
    return audit


# ---------------------------------------------------------------------------
# Proposals (human-gated send)
# ---------------------------------------------------------------------------


@router.get("/proposals", response_model=list[ProposalOut])
async def list_proposals(
    status: AgencyProposalStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _team_id(db, current_user)
    q = select(AgencyProposal).where(AgencyProposal.team_id == team_id)
    if status:
        q = q.where(AgencyProposal.status == status)
    rows = (await db.execute(q.order_by(desc(AgencyProposal.created_at)).limit(limit))).scalars().all()
    return rows


@router.get("/proposals/{proposal_id}", response_model=ProposalDetailOut)
async def get_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _team_proposal(db, await _team_id(db, current_user), proposal_id)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve = send. This is the only path that emails a proposal."""
    team_id = await _team_id(db, current_user)
    proposal = await _team_proposal(db, team_id, proposal_id)
    result = await send_proposal(db, proposal, approver=str(current_user.email))
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "cannot send"))
    return result


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
async def reject_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _team_id(db, current_user)
    proposal = await _team_proposal(db, team_id, proposal_id)
    if proposal.status != AgencyProposalStatus.draft:
        raise HTTPException(status_code=409, detail=f"proposal is {proposal.status}, not draft")
    proposal.status = AgencyProposalStatus.rejected
    md = dict(proposal.meta_data or {})
    md["rejected_by"] = str(current_user.email)
    proposal.meta_data = md
    await db.commit()
    return proposal


# ---------------------------------------------------------------------------
# Projects (the PM agent's objects)
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    status: AgencyProjectStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    team_id = await _team_id(db, current_user)
    q = select(AgencyProject).where(AgencyProject.team_id == team_id)
    if status:
        q = q.where(AgencyProject.status == status)
    rows = (await db.execute(q.order_by(desc(AgencyProject.created_at)).limit(limit))).scalars().all()
    return rows


@router.get("/projects/{project_id}", response_model=ProjectDetailOut)
async def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _team_project(db, await _team_id(db, current_user), project_id)


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Kick off the 14-day pipeline for a won engagement."""
    team_id = await _team_id(db, current_user)
    kickoff = body.kickoff_at or datetime.now(UTC)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)

    lead_id = None
    espo_lead_id = None
    client_email = body.client_email
    if body.proposal_id:
        proposal = await _team_proposal(db, team_id, body.proposal_id)
        lead_id = proposal.lead_id
        espo_lead_id = proposal.espo_lead_id
        client_email = client_email or proposal.recipient_email

    project = AgencyProject(
        team_id=team_id,
        proposal_id=body.proposal_id,
        lead_id=lead_id,
        espo_lead_id=espo_lead_id,
        name=body.name,
        client_email=client_email,
        kickoff_at=kickoff,
        due_at=kickoff + timedelta(days=DELIVERY_DAYS - 1),
        milestones=project_milestones(),
        meta_data={
            "services": [s for s in SERVICES],
            "source": "api",
        },
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/catalog")
async def service_catalog(
    current_user: User = Depends(require_admin),
):
    """The fixed-scope service catalog + pricing the agents quote."""
    return {
        "services": [
            {"slug": s.slug, "name": s.name,
             "price_low_eur": s.price_low_eur, "price_high_eur": s.price_high_eur,
             "summary": s.summary}
            for s in SERVICES.values()
        ],
        "delivery_days": DELIVERY_DAYS,
        "bundle_example_price_range": price_range(list(SERVICES)),
    }
