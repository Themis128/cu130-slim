"""Autonomous-agency tables — audit → proposal → 14-day project pipeline.

The agent-led agency model (see ``.devin/skills/agency-ops``): inbound leads
are triaged against the fixed-scope service catalog, their site is audited
deterministically, a priced proposal is drafted for human approval, and an
approved/won proposal becomes a 14-day project with milestone-driven client
updates. Human approval is the governance gate — nothing here auto-sends.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgencyAuditStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgencyProposalStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"


class AgencyProjectStatus(enum.StrEnum):
    active = "active"
    delivered = "delivered"
    stalled = "stalled"


class AgencyAudit(Base):
    """One deterministic site audit for a lead/domain (the Architect)."""

    __tablename__ = "agency_audits"
    __table_args__ = (
        Index("ix_agency_audits_team_status", "team_id", "status"),
        Index("ix_agency_audits_team_created", "team_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # EspoCRM Lead id when the subject lives in the CRM only (contact-form path)
    espo_lead_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[AgencyAuditStatus] = mapped_column(
        SQLEnum(AgencyAuditStatus, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
        default=AgencyAuditStatus.pending,
        index=True,
    )
    # Overall 0-100 site-health score from the deterministic checks.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Structured findings: [{"check", "severity", "finding", "detail", "service"}]
    findings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Recommended service slugs: ["cloud", "serverless", ...]
    recommended_services: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Human-readable action plan (markdown) shown in the proposal.
    action_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgencyProposal(Base):
    """A priced, fixed-scope proposal drafted from an audit (review tier —
    a human approves before anything is emailed to the lead)."""

    __tablename__ = "agency_proposals"
    __table_args__ = (
        Index("ix_agency_proposals_team_status", "team_id", "status"),
        Index("ix_agency_proposals_audit", "audit_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agency_audits.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    espo_lead_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    # Recipient snapshot at draft time (the human can still edit before send).
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Service slugs + the quoted price range in whole EUR.
    services: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    price_low_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_high_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)

    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[AgencyProposalStatus] = mapped_column(
        SQLEnum(AgencyProposalStatus, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
        default=AgencyProposalStatus.draft,
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class AgencyProject(Base):
    """A won engagement on the 14-day fixed-scope pipeline (the PM agent's
    object). Milestones is a JSONB list of
    {"day": int, "key": str, "label": str, "done": bool, "done_at": iso|null}."""

    __tablename__ = "agency_projects"
    __table_args__ = (Index("ix_agency_projects_team_status", "team_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agency_proposals.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    espo_lead_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    client_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[AgencyProjectStatus] = mapped_column(
        SQLEnum(AgencyProjectStatus, values_callable=lambda o: [e.value for e in o]),
        nullable=False,
        default=AgencyProjectStatus.active,
        index=True,
    )
    milestones: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Generated handoff markdown at day 14.
    handoff_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_client_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
