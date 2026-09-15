"""add web analytics config and events

Revision ID: v4g5h6i7j8k9
Revises: u3e6f7a8b9c0
Create Date: 2026-09-15 07:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "v4g5h6i7j8k9"
down_revision = "u3e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_analytics_configs",
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
        sa.Column("webhook_secret", sa.String(255), nullable=False),
        sa.Column("ga4_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ga4_measurement_id", sa.String(40), nullable=True),
        sa.Column("ga4_api_secret", sa.String(100), nullable=True),
        sa.Column("plausible_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("plausible_domain", sa.String(253), nullable=True),
        sa.Column(
            "plausible_api_url",
            sa.String(253),
            nullable=True,
            server_default=sa.text("'https://plausible.io/api/event'"),
        ),
        sa.Column("plausible_api_key", sa.String(255), nullable=True),
        sa.Column("meta_capi_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("meta_pixel_id", sa.String(40), nullable=True),
        sa.Column("meta_capi_access_token", sa.String(255), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
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
        sa.Index("ix_web_analytics_configs_team", "team_id"),
        sa.Index("ix_web_analytics_configs_domain", "domain"),
    )

    op.create_table(
        "web_analytics_events",
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
        sa.Column(
            "config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("web_analytics_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("session_id", sa.String(120), nullable=True),
        sa.Column("visitor_id", sa.String(120), nullable=True),
        sa.Column("path", sa.String(2048), nullable=True),
        sa.Column("referrer", sa.String(2048), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("locale", sa.String(10), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "forwarded",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index("ix_web_analytics_events_team_time", "team_id", "occurred_at"),
        sa.Index("ix_web_analytics_events_domain_time", "domain", "occurred_at"),
        sa.Index("ix_web_analytics_events_name", "event_name"),
        sa.Index("ix_web_analytics_events_session", "session_id"),
    )


def downgrade() -> None:
    op.drop_table("web_analytics_events")
    op.drop_table("web_analytics_configs")
