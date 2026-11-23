"""add ops_services registry

Central registry for manageable services: docker containers, compose
services, n8n workflows, systemd units, Windows tasks, infra observers.
Pairs with ops_watchers (scheduled jobs) — services get status reports
and on-demand allowlisted actions via the host agent.

Revision ID: sv1c2d3e4f5g
Revises: ow1r2g3e4s5t
Create Date: 2026-11-24 00:10:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "sv1c2d3e4f5g"
down_revision = "ow1r2g3e4s5t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_services",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "tier",
            sa.Enum("docker", "n8n", "systemd", "windows", "infra", "external", name="servicetier"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum("container", "workflow", "service", "task", name="servicekind"),
            nullable=False,
            server_default="service",
        ),
        sa.Column(
            "executor",
            sa.Enum("docker-cli", "systemctl", "windows-task", "n8n-api", "external", name="serviceexecutor"),
            nullable=False,
        ),
        sa.Column(
            "managed_by",
            sa.Enum("platform", "external", name="servicemanagedby"),
            nullable=False,
            server_default="external",
        ),
        sa.Column("target", sa.String(200), nullable=False, server_default=""),
        sa.Column("health_url", sa.String(300), nullable=False, server_default=""),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("health", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reported_by", sa.String(60), nullable=False, server_default=""),
        sa.Column("last_action", sa.String(200), nullable=False, server_default=""),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_action", sa.String(40), nullable=False, server_default=""),
        sa.Column("pending_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_services_status", "ops_services", ["enabled", "managed_by", "status"])


def downgrade() -> None:
    op.drop_index("ix_ops_services_status", table_name="ops_services")
    op.drop_table("ops_services")
    for name in ("servicetier", "servicekind", "serviceexecutor", "servicemanagedby"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
