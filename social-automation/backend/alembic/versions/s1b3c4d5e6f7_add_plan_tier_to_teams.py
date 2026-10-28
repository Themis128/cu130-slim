"""Add plan_tier column to teams.

Revision ID: s1b3c4d5e6f7
Revises: r0a1b2c3d4e5
Create Date: 2026-09-07 22:50:00.000000

Note: This column was added out-of-band before the migration existed.
The upgrade() is idempotent — it checks information_schema before adding
so it works on databases that already have the column.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "s1b3c4d5e6f7"
down_revision = "r0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("teams")]
    if "plan_tier" not in columns:
        op.add_column(
            "teams",
            sa.Column("plan_tier", sa.String(length=20), server_default="free", nullable=False),
        )
    # Backfill any existing rows that somehow have NULL (shouldn't happen with server_default).
    op.execute("UPDATE teams SET plan_tier = 'free' WHERE plan_tier IS NULL")


def downgrade() -> None:
    op.drop_column("teams", "plan_tier")
