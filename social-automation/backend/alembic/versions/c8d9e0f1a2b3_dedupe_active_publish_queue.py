"""dedupe active publish_queue rows + partial unique index

Multiple active (pending/processing) rows for the same
(post_id, social_account_id) caused duplicate external publishes —
each row ran publish_to_platform and overwrote post_targets.platform_post_id.
A partial unique index on the active statuses makes the dedupe atomic.

Existing duplicates are collapsed: earliest created row stays active,
the rest are marked cancelled.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-27 12:30:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Collapse existing duplicate active rows before adding the index:
    # keep the earliest-created row per (post_id, social_account_id),
    # cancel the rest.
    op.execute(
        sa.text(
            """
            UPDATE publish_queue
            SET status = 'cancelled', locked_at = NULL, locked_by = NULL
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY post_id, social_account_id
                               ORDER BY created_at ASC
                           ) AS rn
                    FROM publish_queue
                    WHERE status IN ('pending', 'processing')
                ) dup
                WHERE dup.rn > 1
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX ux_publish_queue_active_target
            ON publish_queue (post_id, social_account_id)
            WHERE status IN ('pending', 'processing')
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_publish_queue_active_target"))
