"""Add ix_analytics_events_platform_event index

Revision ID: b2c3d4e5f6a7
Revises: 21e4c2d4daf5
Create Date: 2026-08-31 06:00:00.000000

Adds the platform_event_id index that was declared in the ORM model
but missing from the initial migration.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "21e4c2d4daf5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import text as sa_text

    # Check if index already exists (may have been created manually)
    conn = op.get_bind()
    result = conn.execute(
        sa_text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_analytics_events_platform_event'"
        )
    )
    if not result.fetchone():
        op.create_index(
            "ix_analytics_events_platform_event",
            "analytics_events",
            ["platform_event_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_analytics_events_platform_event", table_name="analytics_events")
