"""add alert gate columns to ops_incidents

alerted_at / alerted_severity implement the alert-once-per-episode gate:
the ops-agent sweep only posts a Telegram alert for an incident when it has
never alerted for the current episode, or when severity increased since the
last alert. Existing non-open rows are backfilled as already-alerted so the
deploy does not re-post a final burst for the currently-noisy incident set.

Revision ID: g5h6i7j8k9l0
Revises: c2d4e6f8a0b2
Create Date: 2026-11-26 06:45:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "g5h6i7j8k9l0"
down_revision = "c2d4e6f8a0b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ops_incidents", sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ops_incidents", sa.Column("alerted_severity", sa.String(20), nullable=True))
    op.execute(
        "UPDATE ops_incidents SET alerted_at = now(), alerted_severity = severity "
        "WHERE status IN ('triaged','actioned','escalated') AND alerted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("ops_incidents", "alerted_severity")
    op.drop_column("ops_incidents", "alerted_at")
