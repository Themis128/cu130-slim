"""add ad_campaign_snapshots table

Revision ID: a6b7c8d9e0f1
Revises: w1a2b3c4d5e6
Create Date: 2026-09-24 18:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "w1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_campaign_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False, server_default="linkedin"),
        sa.Column("account_id", sa.String(40), nullable=False),
        sa.Column("campaign_id", sa.String(40), nullable=False),
        sa.Column("campaign_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("impressions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("engagements", sa.Integer, nullable=False, server_default="0"),
        sa.Column("spend_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float, nullable=False, server_default="0"),
        sa.Column("engagement_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("cpc_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("budget_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ad_campaign_snapshots_team_id", "ad_campaign_snapshots", ["team_id"])
    op.create_index("ix_ad_campaign_snapshots_team_time", "ad_campaign_snapshots", ["team_id", "captured_at"])
    op.create_index("ix_ad_campaign_snapshots_campaign", "ad_campaign_snapshots", ["campaign_id", "captured_at"])


def downgrade() -> None:
    op.drop_table("ad_campaign_snapshots")
