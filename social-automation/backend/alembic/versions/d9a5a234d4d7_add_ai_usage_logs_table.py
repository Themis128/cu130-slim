"""add ai usage logs table

Revision ID: d9a5a234d4d7
Revises: g3b4c5d6e7f8
Create Date: 2026-08-22 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'd9a5a234d4d7'
down_revision: str | None = 'g3b4c5d6e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'ai_usage_logs' not in inspector.get_table_names():
        op.create_table(
            'ai_usage_logs',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('provider', sa.String(50), nullable=False),
            sa.Column('model', sa.String(200), nullable=False),
            sa.Column('endpoint', sa.String(100), nullable=True),
            sa.Column('prompt_length', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('estimated_tokens', sa.Integer(), nullable=True),
            sa.Column('estimated_neurons', sa.Integer(), nullable=True),
            sa.Column('latency_ms', sa.Integer(), nullable=True),
            sa.Column('success', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_ai_usage_logs_team_id', 'ai_usage_logs', ['team_id'])
        op.create_index('ix_ai_usage_logs_user_id', 'ai_usage_logs', ['user_id'])
        op.create_index('ix_ai_usage_logs_provider', 'ai_usage_logs', ['provider'])
        op.create_index('ix_ai_usage_logs_model', 'ai_usage_logs', ['model'])
        op.create_index('ix_ai_usage_logs_endpoint', 'ai_usage_logs', ['endpoint'])


def downgrade() -> None:
    op.drop_index('ix_ai_usage_logs_team_id', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_user_id', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_provider', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_model', table_name='ai_usage_logs')
    op.drop_index('ix_ai_usage_logs_endpoint', table_name='ai_usage_logs')
    op.drop_table('ai_usage_logs')
