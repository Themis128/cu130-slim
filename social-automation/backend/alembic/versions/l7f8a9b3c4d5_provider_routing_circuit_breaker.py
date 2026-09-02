"""Add provider routing policy, circuit breaker fields, and usage cost columns.

Revision ID: l7f8a9b3c4d5
Revises: k6e7f8a9b2c3
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa


revision = "l7f8a9b3c4d5"
down_revision = "k6e7f8a9b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AIProvider routing policy + circuit breaker
    op.add_column("ai_providers", sa.Column("fallbacks", sa.String(500), nullable=True))
    op.add_column("ai_providers", sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="120"))
    op.add_column("ai_providers", sa.Column("max_retries", sa.Integer, nullable=False, server_default="1"))
    op.add_column("ai_providers", sa.Column("daily_neuron_budget", sa.Integer, nullable=True))
    op.add_column("ai_providers", sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("ai_providers", sa.Column("circuit_open", sa.Boolean, nullable=False, server_default=sa.text("false")))
    op.add_column("ai_providers", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True))

    # AIUsageLog cost tracking
    op.add_column("ai_usage_logs", sa.Column("actual_neurons", sa.Integer, nullable=True))
    op.add_column("ai_usage_logs", sa.Column("estimated_cost", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("ai_usage_logs", "estimated_cost")
    op.drop_column("ai_usage_logs", "actual_neurons")
    op.drop_column("ai_providers", "cooldown_until")
    op.drop_column("ai_providers", "circuit_open")
    op.drop_column("ai_providers", "failure_count")
    op.drop_column("ai_providers", "daily_neuron_budget")
    op.drop_column("ai_providers", "max_retries")
    op.drop_column("ai_providers", "timeout_seconds")
    op.drop_column("ai_providers", "fallbacks")
