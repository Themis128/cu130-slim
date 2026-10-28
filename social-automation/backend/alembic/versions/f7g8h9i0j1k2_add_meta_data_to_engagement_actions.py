"""add meta_data to engagement_actions

Revision ID: f7g8h9i0j1k2
Revises: z9k0l1m2n3o4
Depends on: d3e5f7a9b1c4 (creates engagement_actions)
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f7g8h9i0j1k2"
down_revision = "d3e5f7a9b1c4"
branch_labels = None
# engagement_actions is created on a different branch (d3e5f7a9b1c4);
# without this a fresh `alembic upgrade head` may run this first and fail.
depends_on = "d3e5f7a9b1c4"


def upgrade():
    op.add_column(
        "engagement_actions",
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("engagement_actions", "meta_data")
