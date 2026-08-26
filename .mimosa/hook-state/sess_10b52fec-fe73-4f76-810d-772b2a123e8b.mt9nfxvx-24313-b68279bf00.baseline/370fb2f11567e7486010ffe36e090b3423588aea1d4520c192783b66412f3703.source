"""add ai_providers table

Revision ID: a1b2c3d4e5f6
Revises: d897700d7a90
Create Date: 2026-08-22 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'd897700d7a90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'ai_providers' not in inspector.get_table_names():
        op.create_table(
            'ai_providers',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('name', sa.String(50), nullable=False),
            sa.Column('display_name', sa.String(100), nullable=False),
            sa.Column('api_key_enc', postgresql.BYTEA(), nullable=True),
            sa.Column('base_url', sa.String(300), nullable=False),
            sa.Column('default_model', sa.String(200), nullable=False),
            sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('team_id', 'name', name='uq_ai_provider_team_name'),
        )
        op.create_index('ix_ai_providers_team_id', 'ai_providers', ['team_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_providers_team_id', table_name='ai_providers')
    op.drop_table('ai_providers')
