"""add funnel_conversions — the lead→CTA→checkout join

revision: a1b2c3d4e5f6
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "f9a1b2c3d4e5"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "funnel_conversions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("post_id", UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column("utm_campaign", sa.String(128), nullable=True),
        sa.Column("utm_source", sa.String(64), nullable=True),
        sa.Column("utm_medium", sa.String(64), nullable=True),
        sa.Column("utm_content", sa.String(128), nullable=True),
        sa.Column("event", sa.String(32), nullable=False, server_default="conversion"),
        sa.Column("value", sa.Numeric(10, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),  # polar|stripe|dodo
        sa.Column("provider_ref", sa.String(128), nullable=True),
        sa.Column("meta_data", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_funnel_conversions_email",
                    "funnel_conversions", ["email"])
    op.create_index("ix_funnel_conversions_lead",
                    "funnel_conversions", ["lead_id"])
    op.create_index("ix_funnel_conversions_campaign",
                    "funnel_conversions", ["utm_campaign"])


def downgrade() -> None:
    op.drop_table("funnel_conversions")
