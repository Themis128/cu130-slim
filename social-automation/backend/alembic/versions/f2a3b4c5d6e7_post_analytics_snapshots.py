"""Add post_analytics_snapshots for platform metric sync history

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_analytics_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "social_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("social_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("platform_post_id", sa.String(200), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(40), nullable=False, server_default="linkedin"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_post_analytics_snapshots_team_id", "post_analytics_snapshots", ["team_id"])
    op.create_index("ix_post_analytics_snapshots_post_id", "post_analytics_snapshots", ["post_id"])
    op.create_index("ix_post_analytics_snapshots_social_account_id", "post_analytics_snapshots", ["social_account_id"])
    op.create_index("ix_post_analytics_snapshots_captured_at", "post_analytics_snapshots", ["captured_at"])
    op.create_index(
        "ix_post_analytics_snapshots_team_time",
        "post_analytics_snapshots",
        ["team_id", "captured_at"],
    )
    op.create_index(
        "ix_post_analytics_snapshots_post",
        "post_analytics_snapshots",
        ["post_id", "captured_at"],
    )
    op.create_index(
        "ix_post_analytics_snapshots_account",
        "post_analytics_snapshots",
        ["social_account_id", "captured_at"],
    )
    # Widen platform_event_id for sync keys
    op.alter_column(
        "analytics_events",
        "platform_event_id",
        existing_type=sa.String(length=100),
        type_=sa.String(length=200),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "analytics_events",
        "platform_event_id",
        existing_type=sa.String(length=200),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
    op.drop_table("post_analytics_snapshots")
