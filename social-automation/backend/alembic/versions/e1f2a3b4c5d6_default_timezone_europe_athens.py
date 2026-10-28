"""Default user timezone to Europe/Athens

Revision ID: e1f2a3b4c5d6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET timezone = 'Europe/Athens' "
        "WHERE timezone IS NULL OR timezone = '' OR timezone = 'UTC'"
    )
    op.alter_column(
        "users",
        "timezone",
        existing_type=sa.String(length=50),
        server_default="Europe/Athens",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "timezone",
        existing_type=sa.String(length=50),
        server_default="UTC",
        existing_nullable=False,
    )
