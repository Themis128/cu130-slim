"""add site_traffic_daily table

Swetrix CE daily rollups — cookieless site traffic per owned domain,
upserted by swetrix-sync and exported to lake/socialauto-site-traffic/.

Revision ID: sw3t2026a1b2
Revises: u0a1b2c3d4e5
Create Date: 2026-11-22 00:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw3t2026a1b2"
down_revision = "u0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_traffic_daily",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("swetrix_pid", sa.String(16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("visitors", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pageviews", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("bounce_rate", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("session_duration_sec", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("team_id", "domain", "date", name="uq_site_traffic_daily"),
    )
    op.create_index("ix_site_traffic_daily_team_date", "site_traffic_daily", ["team_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_site_traffic_daily_team_date", table_name="site_traffic_daily")
    op.drop_table("site_traffic_daily")
