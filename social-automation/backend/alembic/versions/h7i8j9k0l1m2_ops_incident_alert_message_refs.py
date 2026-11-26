"""ops_incidents: alert message refs for edit-in-place lifecycle

Revision ID: h7i8j9k0l1m2
Revises: g5h6i7j8k9l0
Create Date: 2026-11-26

Adds alert_chat_id / alert_message_id so the ops-agent can edit the original
Telegram alert in place (resolution one-liner) and auto-delete it after the
resolution grace window — one incident = exactly one message.
"""

import sqlalchemy as sa

from alembic import op

revision = "h7i8j9k0l1m2"
down_revision = "g5h6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ops_incidents", sa.Column("alert_chat_id", sa.String(length=64), nullable=True))
    op.add_column("ops_incidents", sa.Column("alert_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("ops_incidents", "alert_message_id")
    op.drop_column("ops_incidents", "alert_chat_id")
