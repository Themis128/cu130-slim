"""merge_brand_and_account_type_heads

Revision ID: 5d35f29495b9
Revises: h4c5d6e7f8a9, e7f8a9b1c2d3
Create Date: 2026-09-01 05:06:58.784178

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d35f29495b9'
down_revision: Union[str, None] = ('h4c5d6e7f8a9', 'e7f8a9b1c2d3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass