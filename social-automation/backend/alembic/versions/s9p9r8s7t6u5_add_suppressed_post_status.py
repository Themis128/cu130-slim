"""Add 'suppressed' to posts.status enum (guardrail-skipped posts are not failures).

Revision ID: s9p9r8s7t6u5
Revises: tt11rend5ignal
Create Date: 2026-11-09
"""

from alembic import op

revision = "s9p9r8s7t6u5"
down_revision = "tt11rend5ignal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE poststatus ADD VALUE IF NOT EXISTS 'suppressed'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; suppressed rows keep working
    # because the model enum still accepts the stored label.
    pass
