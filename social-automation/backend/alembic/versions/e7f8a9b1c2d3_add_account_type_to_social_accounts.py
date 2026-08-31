"""Add account_type, is_business, parent_account_id to social_accounts.

Revision ID: e7f8a9b1c2d3
Revises: d6e7f8a9b1c2
Create Date: 2026-08-31 12:00:00.000000
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "e7f8a9b1c2d3"
down_revision = "d6e7f8a9b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_accounts", sa.Column("account_type", sa.String(30), server_default="person", nullable=False))
    op.add_column("social_accounts", sa.Column("is_business", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("social_accounts", sa.Column("parent_account_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_social_accounts_team_type", "social_accounts", ["team_id", "is_business"])
    op.create_foreign_key(
        "fk_social_accounts_parent",
        "social_accounts",
        "social_accounts",
        ["parent_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill account_type and is_business from existing meta_data
    op.execute("""
        UPDATE social_accounts
        SET account_type = COALESCE(meta_data->>'account_type', 'person'),
            is_business = CASE
                WHEN meta_data->>'account_type' IN ('organization', 'page', 'business', 'creator') THEN true
                ELSE false
            END
        WHERE meta_data->>'account_type' IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_constraint("fk_social_accounts_parent", "social_accounts", type_="foreignkey")
    op.drop_index("ix_social_accounts_team_type", table_name="social_accounts")
    op.drop_column("social_accounts", "parent_account_id")
    op.drop_column("social_accounts", "is_business")
    op.drop_column("social_accounts", "account_type")
