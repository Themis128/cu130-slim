"""add polar billing columns to teams

Revision ID: x7i8j9k0l1m2
Revises: w6h7i8j9k0l1
Create Date: 2026-09-16 07:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "x7i8j9k0l1m2"
down_revision = "w6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("polar_customer_id", sa.String(64), nullable=True))
    op.add_column("teams", sa.Column("polar_subscription_id", sa.String(64), nullable=True))
    op.create_index("ix_teams_polar_customer_id", "teams", ["polar_customer_id"])
    op.create_index("ix_teams_polar_subscription_id", "teams", ["polar_subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_teams_polar_subscription_id", table_name="teams")
    op.drop_index("ix_teams_polar_customer_id", table_name="teams")
    op.drop_column("teams", "polar_subscription_id")
    op.drop_column("teams", "polar_customer_id")
