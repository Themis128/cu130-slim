"""add agency pipeline tables (audit → proposal → 14-day project)

The agent-led agency model: agency_audits (Architect output),
agency_proposals (human-approved quotes), agency_projects (PM state
machine with day-1/5/10/14 milestones).

Revision ID: c2d4e6f8a0b2
Revises: a4f5e6d7c8b9
Create Date: 2026-10-25 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "c2d4e6f8a0b2"
down_revision = "a4f5e6d7c8b9"
branch_labels = None
depends_on = None

_AUDIT_STATUS = ("pending", "running", "completed", "failed")
_PROPOSAL_STATUS = ("draft", "approved", "rejected", "sent")
_PROJECT_STATUS = ("active", "delivered", "stalled")


def upgrade() -> None:
    op.create_table(
        "agency_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("espo_lead_id", sa.String(40), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_AUDIT_STATUS, name="agencyauditstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("findings", JSONB, nullable=False, server_default="[]"),
        sa.Column("recommended_services", JSONB, nullable=False, server_default="[]"),
        sa.Column("action_plan", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agency_audits_team_status", "agency_audits", ["team_id", "status"])
    op.create_index("ix_agency_audits_team_created", "agency_audits", ["team_id", "created_at"])
    op.create_index("ix_agency_audits_lead_id", "agency_audits", ["lead_id"])
    op.create_index("ix_agency_audits_espo_lead_id", "agency_audits", ["espo_lead_id"])
    op.create_index("ix_agency_audits_status", "agency_audits", ["status"])
    op.create_index("ix_agency_audits_team_id", "agency_audits", ["team_id"])

    op.create_table(
        "agency_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "audit_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agency_audits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("espo_lead_id", sa.String(40), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("recipient_name", sa.String(200), nullable=True),
        sa.Column("services", JSONB, nullable=False, server_default="[]"),
        sa.Column("price_low_eur", sa.Integer(), nullable=True),
        sa.Column("price_high_eur", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(300), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum(*_PROPOSAL_STATUS, name="agencyproposalstatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agency_proposals_team_status", "agency_proposals", ["team_id", "status"])
    op.create_index("ix_agency_proposals_audit", "agency_proposals", ["audit_id"])
    op.create_index("ix_agency_proposals_lead_id", "agency_proposals", ["lead_id"])
    op.create_index("ix_agency_proposals_espo_lead_id", "agency_proposals", ["espo_lead_id"])
    op.create_index("ix_agency_proposals_status", "agency_proposals", ["status"])
    op.create_index("ix_agency_proposals_team_id", "agency_proposals", ["team_id"])

    op.create_table(
        "agency_projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agency_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("espo_lead_id", sa.String(40), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("client_email", sa.String(320), nullable=True),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_PROJECT_STATUS, name="agencyprojectstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("milestones", JSONB, nullable=False, server_default="[]"),
        sa.Column("handoff_md", sa.Text(), nullable=True),
        sa.Column("last_client_update_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agency_projects_team_status", "agency_projects", ["team_id", "status"])
    op.create_index("ix_agency_projects_lead_id", "agency_projects", ["lead_id"])
    op.create_index("ix_agency_projects_espo_lead_id", "agency_projects", ["espo_lead_id"])
    op.create_index("ix_agency_projects_status", "agency_projects", ["status"])
    op.create_index("ix_agency_projects_team_id", "agency_projects", ["team_id"])


def downgrade() -> None:
    op.drop_table("agency_projects")
    op.drop_table("agency_proposals")
    op.drop_table("agency_audits")
    for enum_name in ("agencyprojectstatus", "agencyproposalstatus", "agencyauditstatus"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
