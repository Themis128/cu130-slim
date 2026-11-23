"""add ops_watchers registry

Central registry for every watchdog/auto-healer: celery beats, WSL cron
jobs, host supervisors, docker autoheal, Windows tasks, infra observers.
Enables platform-side status, enable/disable and run-now control.

Revision ID: ow1r2g3e4s5t
Revises: sw3t2026a1b2
Create Date: 2026-11-23 23:45:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "ow1r2g3e4s5t"
down_revision = "sw3t2026a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_watchers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "tier",
            sa.Enum(
                "platform", "host", "windows", "docker", "infra", "external",
                name="watchertier",
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum("watch", "heal", "sync", "report", "janitor", name="watcherkind"),
            nullable=False,
            server_default="watch",
        ),
        sa.Column(
            "executor",
            sa.Enum("celery", "host-agent", "external", name="watcherexecutor"),
            nullable=False,
        ),
        sa.Column(
            "managed_by",
            sa.Enum("platform", "external", name="watchermanagedby"),
            nullable=False,
            server_default="external",
        ),
        sa.Column("command", sa.Text(), nullable=False, server_default=""),
        sa.Column("schedule_cron", sa.String(120), nullable=False, server_default=""),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_exit_code", sa.Integer(), nullable=True),
        sa.Column("last_output_tail", sa.Text(), nullable=False, server_default=""),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ops_watchers_due", "ops_watchers", ["enabled", "managed_by", "executor", "next_due_at"]
    )
    op.create_index("ix_ops_watchers_last_run", "ops_watchers", ["last_finished_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_watchers_last_run", table_name="ops_watchers")
    op.drop_index("ix_ops_watchers_due", table_name="ops_watchers")
    op.drop_table("ops_watchers")
    for name in ("watchertier", "watcherkind", "watcherexecutor", "watchermanagedby"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
