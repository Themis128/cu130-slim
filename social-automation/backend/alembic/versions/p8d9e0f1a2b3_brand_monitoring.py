"""Add brand_mentions and competitor_snapshots tables.

Revision ID: p8d9e0f1a2b3
Revises: o7c8d9e0f1a2
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "p8d9e0f1a2b3"
down_revision = "o7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_mentions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("author", sa.String(255)),
        sa.Column("content", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("sentiment", sa.String(20)),
        sa.Column("sentiment_score", sa.Float),
        sa.Column("engagement", sa.Integer, server_default="0"),
        sa.Column("mentioned_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("extra_data", JSONB, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_brand_mentions_brand", "brand_mentions", ["brand_id"])
    op.create_index("ix_brand_mentions_platform", "brand_mentions", ["platform"])
    op.create_index("ix_brand_mentions_sentiment", "brand_mentions", ["sentiment"])

    op.create_table(
        "competitor_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("competitor_name", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("follower_count", sa.Integer),
        sa.Column("engagement_rate", sa.Float),
        sa.Column("post_count", sa.Integer),
        sa.Column("top_post_content", sa.Text),
        sa.Column("top_post_engagement", sa.Integer),
        sa.Column("snapshot_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("extra_data", JSONB, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_competitor_snapshots_brand", "competitor_snapshots", ["brand_id"])
    op.create_index("ix_competitor_snapshots_competitor", "competitor_snapshots", ["competitor_name"])


def downgrade() -> None:
    op.drop_table("competitor_snapshots")
    op.drop_table("brand_mentions")
