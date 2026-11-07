"""add trend_signals table

Persisted niche-scored trend signals from the Google Trends and TikTok
Creative Center feeds — the datalake layer for data-driven content and
engagement decisions.

Revision ID: tt11rend5ignal
Revises: a2b3c4d5e6f7
Create Date: 2026-11-08 01:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "tt11rend5ignal"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trend_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("geo", sa.String(10), nullable=False),
        sa.Column("traffic", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("classification", sa.String(20), nullable=False),
        sa.Column("swot", sa.String(5), nullable=False, server_default=""),
        sa.Column("period_key", sa.String(20), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("source", "query", "geo", "period_key", name="uq_trend_signal"),
    )
    op.create_index("ix_trend_signals_source", "trend_signals", ["source"])
    op.create_index("ix_trend_signals_captured", "trend_signals", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_trend_signals_captured")
    op.drop_index("ix_trend_signals_source")
    op.drop_table("trend_signals")
