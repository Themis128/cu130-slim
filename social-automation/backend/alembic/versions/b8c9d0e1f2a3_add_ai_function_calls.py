"""add ai_function_calls audit table

Revision ID: b8c9d0e1f2a3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-31

AI-function audit (Databricks-pattern): every DMR-backed call lands a row —
fn, model, input sha, outcome, latency. Auditable-outcomes mandate +
fine-tune corpus.
"""
import sqlalchemy as sa

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "f7a8b9c0d1e2"


def upgrade() -> None:
    op.create_table(
        "ai_function_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fn", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("input_sha", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_function_calls_fn", "ai_function_calls", ["fn"])
    op.create_index("ix_ai_function_calls_input_sha", "ai_function_calls", ["input_sha"])
    op.create_index("ix_ai_function_calls_created_at", "ai_function_calls", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_function_calls")
