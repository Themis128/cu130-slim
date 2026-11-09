"""add ops_incidents table (AgentOps incident object)

Shared incident store for the ops-agent loop: detectors report via
report_incident(), the ops-agent Celery sweep triages/acts/escalates.
Guarded by has_table — environments that already created the table
manually (host psql fallback path) skip creation.

Revision ID: t1u2v3w4x5y6
Revises: s9p9r8s7t6u5
Create Date: 2026-11-10 01:40:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "t1u2v3w4x5y6"
down_revision = "s9p9r8s7t6u5"
branch_labels = None
depends_on = None

_INCIDENT_STATUS = ("open", "triaged", "actioned", "resolved", "escalated")
_INCIDENT_SEVERITY = ("info", "ops", "critical")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("ops_incidents"):
        return
    op.create_table(
        "ops_incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("target", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "severity",
            sa.Enum(*_INCIDENT_SEVERITY, name="incidentseverity"),
            nullable=False,
            server_default="ops",
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "status",
            sa.Enum(*_INCIDENT_STATUS, name="incidentstatus"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("rca", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_action", sa.String(200), nullable=False, server_default=""),
        sa.Column("action_taken", sa.String(500), nullable=False, server_default=""),
        sa.Column("action_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_incidents_status", "ops_incidents", ["status"])
    op.create_index("ix_ops_incidents_last_seen", "ops_incidents", ["last_seen"])
    op.create_index("ix_ops_incidents_status_fingerprint", "ops_incidents", ["status", "fingerprint"])
    op.create_index("ix_ops_incidents_status_last_seen", "ops_incidents", ["status", "last_seen"])


def downgrade() -> None:
    op.drop_table("ops_incidents")
