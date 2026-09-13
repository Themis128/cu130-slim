"""add digital cards table

Revision ID: u3e6f7a8b9c0
Revises: l6f7a8b9c0d1
Create Date: 2026-09-13 16:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "u3e6f7a8b9c0"
down_revision = "l6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "digital_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("tagline", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("primary_color", sa.String(20), nullable=True),
        sa.Column("accent_color", sa.String(20), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("social_links", postgresql.JSONB, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("services", postgresql.JSONB, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("share_token", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("view_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("contact_save_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("share_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("meta_data", postgresql.JSONB, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_digital_cards_team", "digital_cards", ["team_id"])
    op.create_index("ix_digital_cards_token", "digital_cards", ["share_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_digital_cards_token", table_name="digital_cards")
    op.drop_index("ix_digital_cards_team", table_name="digital_cards")
    op.drop_table("digital_cards")
