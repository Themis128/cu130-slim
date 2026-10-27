"""add meta_data to engagement_actions

Revision ID: f7g8h9i0j1k2
Revises: z9k0l1m2n3o4
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f7g8h9i0j1k2'
down_revision = 'z9k0l1m2n3o4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'engagement_actions',
        sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default='{}'),
    )


def downgrade():
    op.drop_column('engagement_actions', 'meta_data')
