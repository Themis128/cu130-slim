"""add polar_discount_code column to teams

Stores the Polar discount code captured at user registration so the
checkout endpoint can resolve it to a discount_id and auto-apply it.

Revision ID: z9k0l1m2n3o4
Revises: y8j9k0l1m2n3
Create Date: 2026-09-18 08:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "z9k0l1m2n3o4"
down_revision = "y8j9k0l1m2n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("polar_discount_code", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("teams", "polar_discount_code")
