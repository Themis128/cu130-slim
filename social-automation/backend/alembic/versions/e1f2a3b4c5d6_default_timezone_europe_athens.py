"""Default user timezone to Europe/Athens

Revision ID: e1f2a3b4c5d6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
