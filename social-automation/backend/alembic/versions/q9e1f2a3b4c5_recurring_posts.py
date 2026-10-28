"""Add recurring post fields to the posts table.

Adds is_recurring, recurrence_pattern, recurrence_interval, recurrence_count,
recurrence_max, recurrence_parent_id, and next_recurrence_at columns.
Also creates the recurrencepattern enum type.

Revision ID: q9e1f2a3b4c5
Revises: p8d9e0f1a2b3
Create Date: 2026-09-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "q9e1f2a3b4c5"
down_revision = "p8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the recurrencepattern enum type
    recurrence_pattern = sa.Enum("none", "daily", "weekly", "monthly", name="recurrencepattern")
    recurrence_pattern.create(op.get_bind(), checkfirst=True)

    op.add_column("posts", sa.Column("is_recurring", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("posts", sa.Column("recurrence_pattern", recurrence_pattern, server_default="none", nullable=False))
    op.add_column("posts", sa.Column("recurrence_interval", sa.Integer(), server_default="0", nullable=False))
    op.add_column("posts", sa.Column("recurrence_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("posts", sa.Column("recurrence_max", sa.Integer(), server_default="0", nullable=False))
    op.add_column("posts", sa.Column("recurrence_parent_id", UUID(as_uuid=True), nullable=True))
    op.add_column("posts", sa.Column("next_recurrence_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_posts_recurrence_parent", "posts", ["recurrence_parent_id"])
    op.create_index("ix_posts_next_recurrence", "posts", ["next_recurrence_at"])
    op.create_foreign_key(
        "fk_posts_recurrence_parent_id",
        "posts",
        "posts",
        ["recurrence_parent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_recurrence_parent_id", "posts", type_="foreignkey")
    op.drop_index("ix_posts_next_recurrence", table_name="posts")
    op.drop_index("ix_posts_recurrence_parent", table_name="posts")
    op.drop_column("posts", "next_recurrence_at")
    op.drop_column("posts", "recurrence_parent_id")
    op.drop_column("posts", "recurrence_max")
    op.drop_column("posts", "recurrence_count")
    op.drop_column("posts", "recurrence_interval")
    op.drop_column("posts", "recurrence_pattern")
    op.drop_column("posts", "is_recurring")

    sa.Enum(name="recurrencepattern").drop(op.get_bind(), checkfirst=True)
