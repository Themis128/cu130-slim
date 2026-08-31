"""Add 2FA and notification preferences to users.

Revision ID: d6e7f8a9b1c2
Revises: c5d6e7f8a9b0
Create Date: 2026-08-31 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "d6e7f8a9b1c2"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("two_factor_enabled", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("two_factor_secret", sa.String(64), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "notification_preferences",
            JSONB(),
            server_default='{}',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "notification_preferences")
    op.drop_column("users", "two_factor_secret")
    op.drop_column("users", "two_factor_enabled")
