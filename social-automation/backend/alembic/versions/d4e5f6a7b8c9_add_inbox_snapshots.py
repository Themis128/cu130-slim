"""add inbox_snapshots table

Point-in-time captures of the unified inbox (Messenger, personal Messenger,
Instagram DMs, WhatsApp) so reports can count unanswered conversations and
track response-time trends without live provider calls.

Revision ID: d4e5f6a7b8c9
Revises: x9y0z1a2b3c4
Create Date: 2026-11-18 01:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "x9y0z1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("account_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("account_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("thread_id", sa.String(300), nullable=True),
        sa.Column("sender_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("preview", sa.Text(), nullable=True),
        sa.Column("unread", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("e2ee", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_activity_at", sa.String(64), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inbox_snapshots_team_id", "inbox_snapshots", ["team_id"])
    op.create_index("ix_inbox_snapshots_batch_id", "inbox_snapshots", ["batch_id"])
    op.create_index("ix_inbox_snapshots_team_time", "inbox_snapshots", ["team_id", "captured_at"])
    op.create_index(
        "ix_inbox_snapshots_thread",
        "inbox_snapshots",
        ["platform", "thread_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_table("inbox_snapshots")
