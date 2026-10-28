"""Add onboarding_completed to users.

Revision ID: r0a1b2c3d4e5
Revises: q9e1f2a3b4c5
Create Date: 2026-09-15 12:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "r0a1b2c3d4e5"
down_revision = "q9e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed")
