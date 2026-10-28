"""Add follower_snapshots table for follower-growth time series.

Revision ID: k6e7f8a9b2c3
Revises: j5d6e7f8a9b1
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "k6e7f8a9b2c3"
down_revision = "j5d6e7f8a9b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "follower_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("social_account_id", UUID(as_uuid=True), sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("followers", sa.Integer, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, index=True, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_follower_snapshots_account_time", "follower_snapshots", ["social_account_id", "captured_at"])
    op.create_index("ix_follower_snapshots_team_time", "follower_snapshots", ["team_id", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_follower_snapshots_team_time", table_name="follower_snapshots")
    op.drop_index("ix_follower_snapshots_account_time", table_name="follower_snapshots")
    op.drop_table("follower_snapshots")
