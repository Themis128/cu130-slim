"""add meta_data to engagement_actions (v2 — live head)

Revision ID: c9d8e7f6a5b4
Revises: k2f9a1b4c6d8
Create Date: 2026-11-12

The dispatch_engagements task writes meta_data (gate_notified,
skip_count) onto EngagementAction rows, but the model lacked the
mapped column and the earlier f7g8h9i0j1k2 migration sits on a dead
branch that never applied — every run crashed with
AttributeError: 'EngagementAction' object has no attribute 'meta_data'.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c9d8e7f6a5b4"
down_revision = "k2f9a1b4c6d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "engagement_actions",
        sa.Column(
            "meta_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade():
    op.drop_column("engagement_actions", "meta_data")
