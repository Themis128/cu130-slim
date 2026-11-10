"""add error_class to post_targets

Revision ID: a1b2c3d4e5f6
Revises: t1u2v3w4x5y6
Create Date: 2026-11-10

Persisted publish-error taxonomy (transient/terminal/unknown) so the
retry sweep can distinguish retryable failures from dead credentials
and deterministic rejections instead of substring-guessing.
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("post_targets", sa.Column("error_class", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("post_targets", "error_class")
