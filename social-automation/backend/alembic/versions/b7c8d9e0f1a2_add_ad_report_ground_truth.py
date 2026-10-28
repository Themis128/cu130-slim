"""add ad_daily_metrics + ad_demographic_segments (report ground truth)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-09-26 16:30:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_daily_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False, server_default="linkedin"),
        sa.Column("account_id", sa.String(40), nullable=False),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("campaign_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("campaign_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("ad_set_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("ad_set_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("ad_id", sa.String(60), nullable=False, server_default=""),
        sa.Column("ad_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("placement", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default=""),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("impressions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("engagements", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conversions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reach", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicks_to_landing_page", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicks_to_linkedin_page", sa.Integer, nullable=False, server_default="0"),
        sa.Column("spend_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float, nullable=False, server_default="0"),
        sa.Column("cpc_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("cpm_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("engagement_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("budget_eur", sa.Float, nullable=False, server_default="0"),
        sa.Column("source", sa.String(40), nullable=False, server_default="report_csv"),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "account_id", "report_type", "campaign_id", "ad_set_id",
            "ad_id", "placement", "day",
            name="uq_ad_daily_metrics_grain",
        ),
    )
    op.create_index("ix_ad_daily_metrics_team_id", "ad_daily_metrics", ["team_id"])
    op.create_index("ix_ad_daily_metrics_team_time", "ad_daily_metrics", ["team_id", "day"])
    op.create_index("ix_ad_daily_metrics_ad_set", "ad_daily_metrics", ["ad_set_id", "day"])

    op.create_table(
        "ad_demographic_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False, server_default="linkedin"),
        sa.Column("account_id", sa.String(40), nullable=False),
        sa.Column("segment_type", sa.String(60), nullable=False),
        sa.Column("segment_value", sa.String(300), nullable=False),
        sa.Column("window_start", sa.Date, nullable=False),
        sa.Column("window_end", sa.Date, nullable=False),
        sa.Column("impressions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conversions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float, nullable=False, server_default="0"),
        sa.Column("pct_impressions", sa.Float, nullable=False, server_default="0"),
        sa.Column("pct_clicks", sa.Float, nullable=False, server_default="0"),
        sa.Column("source", sa.String(40), nullable=False, server_default="report_csv"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "account_id", "segment_type", "segment_value",
            "window_start", "window_end",
            name="uq_ad_demo_segments_grain",
        ),
    )
    op.create_index("ix_ad_demographic_segments_team_id", "ad_demographic_segments", ["team_id"])
    op.create_index("ix_ad_demo_segments_type", "ad_demographic_segments", ["segment_type", "impressions"])


def downgrade() -> None:
    op.drop_table("ad_demographic_segments")
    op.drop_table("ad_daily_metrics")
