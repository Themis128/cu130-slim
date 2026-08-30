"""Add minio to storage_backend enum

Revision ID: 21e4c2d4daf5
Revises: d9a5a234d4d7
Create Date: 2026-08-31 02:30:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "21e4c2d4daf5"
down_revision = "9c222774bd04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add 'minio' to the storage_backend enum type."""
    op.execute(
        "ALTER TYPE storagebackend ADD VALUE IF NOT EXISTS 'minio'"
    )


def downgrade() -> None:
    """Remove 'minio' from the storage_backend enum type.

    PostgreSQL does not support removing a value from an enum type directly.
    To downgrade, the enum type would need to be recreated without the value.
    This is a no-op for safety — the 'minio' value is harmless if unused.
    """
    pass
