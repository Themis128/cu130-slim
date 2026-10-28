"""add updated_at to post_targets

post_targets had no timestamp for failed/skipped outcomes — only
published_at on success. Metrics (and anything else needing an outcome
window) need a last-write timestamp. Filled automatically by ORM
onupdate; existing rows stay NULL so old outcomes do not pollute
7-day windows after this migration.

Revision ID: a4f5e6d7c8b9
Revises: h8i9j0k1l2m3
Create Date: 2026-10-19 16:30:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a4f5e6d7c8b9"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "post_targets",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # For already-published rows the publish time IS the outcome time —
    # better than NULL for the 7-day window.
    op.execute(
        "UPDATE post_targets SET updated_at = published_at "
        "WHERE published_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("post_targets", "updated_at")
