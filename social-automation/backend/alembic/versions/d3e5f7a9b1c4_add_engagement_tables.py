"""add engagement tables

Revision ID: d3e5f7a9b1c4
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d3e5f7a9b1c4"
down_revision = ("5d35f29495b9", "c2d4e6f8a0b2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engagement_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("author_handle", sa.String(120), nullable=False),
        sa.Column("author_name", sa.String(120), server_default=""),
        sa.Column("post_id", sa.String(120), nullable=False, unique=True),
        sa.Column("post_text", sa.Text, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connection_tier", sa.String(10), server_default="medium"),
        sa.Column("is_followed", sa.Boolean, server_default="true"),
        sa.Column("is_connection", sa.Boolean, server_default="false"),
        sa.Column("in_crm", sa.Boolean, server_default="false"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("scored", sa.Boolean, server_default="false"),
        sa.Column("meta_data", postgresql.JSONB, server_default="{}"),
    )
    op.create_table(
        "engagement_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("target_post_id", sa.String(120), nullable=False),
        sa.Column("target_author", sa.String(120), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("jitter_seconds", sa.Integer, server_default="0"),
        sa.Column("comment_draft", sa.Text),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("acted_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("engagement_actions")
    op.drop_table("engagement_candidates")
