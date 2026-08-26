"""add scheduled posts partial index

Revision ID: d897700d7a90
Revises: d90da9214372
Create Date: 2026-08-20 20:53:29.379830

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd897700d7a90'
down_revision: str | None = 'd90da9214372'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_posts_scheduled ON posts (scheduled_at) WHERE status = 'scheduled'::poststatus")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_posts_scheduled")
