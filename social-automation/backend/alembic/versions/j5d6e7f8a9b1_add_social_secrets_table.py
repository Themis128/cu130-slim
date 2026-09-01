"""Add social_secrets table for Cloudflare SSM with local failover.

Revision ID: j5d6e7f8a9b1
Revises: i5d6e7f8a9b0
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "j5d6e7f8a9b1"
down_revision = "i5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(255), nullable=False, unique=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_social_secrets_key", "social_secrets", ["key"])


def downgrade() -> None:
    op.drop_index("ix_social_secrets_key", table_name="social_secrets")
    op.drop_table("social_secrets")
