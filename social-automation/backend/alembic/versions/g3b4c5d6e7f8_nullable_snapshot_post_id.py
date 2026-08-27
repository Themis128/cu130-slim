"""Allow null post_id on analytics snapshots for LinkedIn-discovered posts

Revision ID: g3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE post_analytics_snapshots DROP CONSTRAINT IF EXISTS uq_post_analytics_snapshot_moment")
    op.drop_constraint(
        "fk_post_analytics_snapshots_post_id_posts",
        "post_analytics_snapshots",
        type_="foreignkey",
    )
    op.alter_column(
        "post_analytics_snapshots",
        "post_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_post_analytics_snapshots_post_id_posts",
        "post_analytics_snapshots",
        "posts",
        ["post_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_post_analytics_snapshots_platform_post",
        "post_analytics_snapshots",
        ["platform_post_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_post_analytics_snapshots_platform_post", table_name="post_analytics_snapshots")
    op.drop_constraint(
        "fk_post_analytics_snapshots_post_id_posts",
        "post_analytics_snapshots",
        type_="foreignkey",
    )
    op.execute("DELETE FROM post_analytics_snapshots WHERE post_id IS NULL")
    op.alter_column(
        "post_analytics_snapshots",
        "post_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_post_analytics_snapshots_post_id_posts",
        "post_analytics_snapshots",
        "posts",
        ["post_id"],
        ["id"],
        ondelete="CASCADE",
    )
