"""add linkedin ad intel tables

Ad Library watch results (linkedin_ad_intel) + watchlist config
(linkedin_intel_watch) for the weekly competitor-intel beat task.

Revision ID: a2b3c4d5e6f7
Revises: f9a1b2c3d4e5
Create Date: 2026-11-07 00:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f9a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linkedin_ad_intel",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ad_url", sa.String(500), nullable=False, unique=True),
        sa.Column("advertiser", sa.String(300), nullable=False, server_default=""),
        sa.Column("ad_payer", sa.String(300), nullable=False, server_default=""),
        sa.Column("advertiser_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("impressions_from", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impressions_to", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_impression_at", sa.Integer(), nullable=True),
        sa.Column("latest_impression_at", sa.Integer(), nullable=True),
        sa.Column("countries", JSONB(), nullable=False, server_default="[]"),
        sa.Column("targeting", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_keyword", sa.String(300), nullable=False, server_default=""),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("miss_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_linkedin_ad_intel_advertiser", "linkedin_ad_intel", ["advertiser"])
    op.create_index("ix_linkedin_ad_intel_status_seen", "linkedin_ad_intel", ["status", "last_seen_at"])
    op.create_index("ix_linkedin_ad_intel_team_time", "linkedin_ad_intel", ["team_id", "last_seen_at"])
    op.create_index("ix_linkedin_ad_intel_team_id", "linkedin_ad_intel", ["team_id"])

    op.create_table(
        "linkedin_intel_watch",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("value", sa.String(300), nullable=False),
        sa.Column("country", sa.String(10), nullable=False, server_default="GR"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("linkedin_intel_watch")
    op.drop_table("linkedin_ad_intel")
