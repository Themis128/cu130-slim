"""Add cancelled to queue_status enum.

Revision ID: c5d6e7f8a9b0
Revises: h4c5d6e7f8a9
Create Date: 2026-08-31 09:30:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "h4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add 'cancelled' to the queue_status enum."""
    op.execute(
        "ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'cancelled'"
    )


def downgrade() -> None:
    """Remove 'cancelled' from the queue_status enum.

    Note: PostgreSQL does not support removing enum values directly.
    This is a no-op downgrade — the extra value is harmless.
    """
    pass
