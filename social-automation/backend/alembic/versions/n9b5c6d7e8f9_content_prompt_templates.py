"""Add content_prompt_templates table for AI content generation templates.

Revision ID: n9b5c6d7e8f9
Revises: m8a9b4c5d6e7
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision = "n9b5c6d7e8f9"
down_revision = "m8a9b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_prompt_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("pillar_id", UUID(as_uuid=True), sa.ForeignKey("pillars.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(30), nullable=True),
        sa.Column("tone", sa.String(50), nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("user_prompt_template", sa.Text, nullable=False),
        sa.Column("variables", ARRAY(sa.String), nullable=False, server_default=sa.text("ARRAY[]::varchar[]")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_content_prompt_templates_team", "content_prompt_templates", ["team_id"])
    op.create_index("ix_content_prompt_templates_pillar", "content_prompt_templates", ["pillar_id"])


def downgrade() -> None:
    op.drop_index("ix_content_prompt_templates_pillar", table_name="content_prompt_templates")
    op.drop_index("ix_content_prompt_templates_team", table_name="content_prompt_templates")
    op.drop_table("content_prompt_templates")
