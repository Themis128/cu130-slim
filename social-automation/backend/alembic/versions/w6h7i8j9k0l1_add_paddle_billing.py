"""add paddle billing fields to teams + billing_events table

Revision ID: w6h7i8j9k0l1
Revises: v4g5h6i7j8k9
Create Date: 2026-09-15 18:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "w6h7i8j9k0l1"
down_revision = "v4g5h6i7j8k9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("paddle_customer_id", sa.String(64), nullable=True))
    op.add_column("teams", sa.Column("paddle_subscription_id", sa.String(64), nullable=True))
    op.add_column(
        "teams",
        sa.Column("subscription_status", sa.String(20), nullable=False, server_default="none"),
    )
    op.add_column(
        "teams",
        sa.Column("subscription_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_teams_paddle_customer_id", "teams", ["paddle_customer_id"])
    op.create_index("ix_teams_paddle_subscription_id", "teams", ["paddle_subscription_id"])

    op.create_table(
        "billing_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_billing_events_event_id", "billing_events", ["event_id"], unique=True)
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("billing_events")
    op.drop_index("ix_teams_paddle_subscription_id", "teams")
    op.drop_index("ix_teams_paddle_customer_id", "teams")
    op.drop_column("teams", "subscription_period_end")
    op.drop_column("teams", "subscription_status")
    op.drop_column("teams", "paddle_subscription_id")
    op.drop_column("teams", "paddle_customer_id")
