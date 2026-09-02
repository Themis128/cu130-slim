"""Add approval workflow (REVIEW/APPROVED), PostComment, Pillar, ContentBrief models.

Revision ID: m8a9b4c5d6e7
Revises: l7f8a9b3c4d5
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision = "m8a9b4c5d6e7"
down_revision = "l7f8a9b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add REVIEW and APPROVED to posts.status enum
    op.execute("ALTER TYPE poststatus ADD VALUE IF NOT EXISTS 'review'")
    op.execute("ALTER TYPE poststatus ADD VALUE IF NOT EXISTS 'approved'")

    # Pillar table (create before adding FK to posts)
    op.create_table(
        "pillars",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String(20), nullable=False, server_default="#6366f1"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_pillars_team", "pillars", ["team_id"])

    # ContentBrief table
    op.create_table(
        "content_briefs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("pillar_id", UUID(as_uuid=True), sa.ForeignKey("pillars.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("outline", sa.Text, nullable=True),
        sa.Column("target_platforms", ARRAY(sa.String), nullable=False, server_default=sa.text("ARRAY[]::varchar[]")),
        sa.Column("tone", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_content_briefs_team_pillar", "content_briefs", ["team_id", "pillar_id"])

    # PostComment table
    op.create_table(
        "post_comments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("post_id", UUID(as_uuid=True), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_post_comments_post", "post_comments", ["post_id", "created_at"])

    # Post: pillar_id + content_brief_id (add after tables exist)
    op.add_column("posts", sa.Column("pillar_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posts", sa.Column("content_brief_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_posts_pillar_id_pillars", "posts", "pillars", ["pillar_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_posts_content_brief_id", "posts", "content_briefs", ["content_brief_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_posts_pillar_id", "posts", ["pillar_id"])
    op.create_index("ix_posts_content_brief_id", "posts", ["content_brief_id"])


def downgrade() -> None:
    op.drop_index("ix_posts_content_brief_id", table_name="posts")
    op.drop_index("ix_posts_pillar_id", table_name="posts")
    op.drop_constraint("fk_posts_content_brief_id", "posts", type_="foreignkey")
    op.drop_constraint("fk_posts_pillar_id_pillars", "posts", type_="foreignkey")
    op.drop_column("posts", "content_brief_id")
    op.drop_column("posts", "pillar_id")
    op.drop_index("ix_post_comments_post", table_name="post_comments")
    op.drop_table("post_comments")
    op.drop_index("ix_content_briefs_team_pillar", table_name="content_briefs")
    op.drop_table("content_briefs")
    op.drop_index("ix_pillars_team", table_name="pillars")
    op.drop_table("pillars")
    # NOTE: enum values cannot be removed from a Postgres enum type
