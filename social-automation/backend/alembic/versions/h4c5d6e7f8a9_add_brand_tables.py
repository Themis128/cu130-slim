"""Add brand identity tables

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
Create Date: 2026-08-31

Creates:
- brands (one per team)
- brand_voices (tone, banned phrases, messaging pillars)
- brand_visuals (colors, fonts, logo)
- brand_guidelines (compiled, shareable)
- brand_assets (logos, templates, OG images)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h4c5d6e7f8a9"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(200), nullable=True),
        sa.Column("positioning_statement", sa.Text(), nullable=True),
        sa.Column("mission", sa.Text(), nullable=True),
        sa.Column("values", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("target_audience", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("competitor_names", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("tagline", sa.String(300), nullable=True),
        sa.Column("website_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", name="uq_brands_team"),
    )
    op.create_index("ix_brands_team", "brands", ["team_id"])

    op.create_table(
        "brand_voices",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tone_dimensions", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("messaging_pillars", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("banned_phrases", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("preferred_phrases", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("example_content", sa.Text(), nullable=True),
        sa.Column("voice_signature", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("brand_id", name="uq_brand_voices_brand"),
    )
    op.create_index("ix_brand_voices_brand", "brand_voices", ["brand_id"])

    op.create_table(
        "brand_visuals",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("primary_color", sa.String(20), nullable=True),
        sa.Column("accent_color", sa.String(20), nullable=True),
        sa.Column("neutral_colors", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("font_heading", sa.String(200), nullable=True),
        sa.Column("font_body", sa.String(200), nullable=True),
        sa.Column("type_scale", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("logo_variants", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("image_style", sa.Text(), nullable=True),
        sa.Column("photography_direction", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("brand_id", name="uq_brand_visuals_brand"),
    )
    op.create_index("ix_brand_visuals_brand", "brand_visuals", ["brand_id"])

    op.create_table(
        "brand_guidelines",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("share_token", sa.String(64), nullable=True, unique=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("brand_id", name="uq_brand_guidelines_brand"),
    )
    op.create_index("ix_brand_guidelines_brand", "brand_guidelines", ["brand_id"])

    op.create_table(
        "brand_assets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brand_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_asset_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asset_type", sa.String(30), nullable=False, server_default=sa.text("'other'")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("asset_metadata", sa.dialects.postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_brand_assets_brand", "brand_assets", ["brand_id"])
    op.create_index("ix_brand_assets_type", "brand_assets", ["asset_type"])


def downgrade() -> None:
    op.drop_table("brand_assets")
    op.drop_table("brand_guidelines")
    op.drop_table("brand_visuals")
    op.drop_table("brand_voices")
    op.drop_table("brands")
