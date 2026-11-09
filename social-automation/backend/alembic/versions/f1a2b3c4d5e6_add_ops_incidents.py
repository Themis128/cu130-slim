"""add ops_incidents table

Shared incident object for the AgentOps loop: detectors report, the
ops-agent Celery sweep triages/diagnoses/escalates.

Revision ID: f1a2b3c4d5e6
Revises: s9p9r8s7t6u5
Create Date: 2026-11-09 23:30:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "s9p9r8s7t6u5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("target", sa.String(300), nullable=False, server_default=""),
        sa.Column("severity", sa.String(20), nullable=False, server_default="ops"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("rca", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed_action", sa.String(200), nullable=False, server_default=""),
        sa.Column("action_taken", sa.String(500), nullable=False, server_default=""),
        sa.Column("action_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ops_incidents_fingerprint", "ops_incidents", ["fingerprint"])
    op.create_index("ix_ops_incidents_status", "ops_incidents", ["status"])
    op.create_index("ix_ops_incidents_last_seen", "ops_incidents", ["last_seen"])
    op.create_index("ix_ops_incidents_status_fingerprint", "ops_incidents", ["status", "fingerprint"])
    op.create_index("ix_ops_incidents_status_last_seen", "ops_incidents", ["status", "last_seen"])


def downgrade() -> None:
    op.drop_table("ops_incidents")
