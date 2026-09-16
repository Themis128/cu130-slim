"""add dodo billing columns to teams

Revision ID: y8j9k0l1m2n3
Revises: x7i8j9k0l1m2
Create Date: 2026-09-16 08:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "y8j9k0l1m2n3"
down_revision = "x7i8j9k0l1m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("dodo_customer_id", sa.String(64), nullable=True))
    op.add_column("teams", sa.Column("dodo_subscription_id", sa.String(64), nullable=True))
    op.create_index("ix_teams_dodo_customer_id", "teams", ["dodo_customer_id"])
    op.create_index("ix_teams_dodo_subscription_id", "teams", ["dodo_subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_teams_dodo_subscription_id", table_name="teams")
    op.drop_index("ix_teams_dodo_customer_id", table_name="teams")
    op.drop_column("teams", "dodo_subscription_id")
    op.drop_column("teams", "dodo_customer_id")
