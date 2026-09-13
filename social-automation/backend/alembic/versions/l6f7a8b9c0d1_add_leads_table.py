"""Add leads table for Meta lead capture

Revision ID: l6f7a8b9c0d1
Revises: t2c4d5e6f7a8
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "l6f7a8b9c0d1"
down_revision = "t2c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Native PG enums: create once via SQL, then bind with create_type=False.
    # sa.Enum(create_type=False) still emits CREATE TYPE on table create;
    # postgresql.ENUM(..., create_type=False) is required under asyncpg.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE leadsource AS ENUM (
                'whatsapp_flow', 'whatsapp_dm', 'facebook_messenger', 'instagram_dm'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE leadinterest AS ENUM ('cloud', 'growth', 'audit');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE leadcompanysize AS ENUM (
                'solo', '2-5', '6-20', '21-50', '51-200', '200+'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    leadsource = postgresql.ENUM(
        "whatsapp_flow",
        "whatsapp_dm",
        "facebook_messenger",
        "instagram_dm",
        name="leadsource",
        create_type=False,
    )
    leadinterest = postgresql.ENUM(
        "cloud",
        "growth",
        "audit",
        name="leadinterest",
        create_type=False,
    )
    leadcompanysize = postgresql.ENUM(
        "solo",
        "2-5",
        "6-20",
        "21-50",
        "51-200",
        "200+",
        name="leadcompanysize",
        create_type=False,
    )

    op.create_table(
        "leads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", leadsource, nullable=False),
        sa.Column(
            "social_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("social_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("company_size", leadcompanysize, nullable=True),
        sa.Column("interest", leadinterest, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "meta_data",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
    )

    op.create_index("ix_leads_team_created", "leads", ["team_id", "created_at"])
    op.create_index("ix_leads_team_email", "leads", ["team_id", "email"])
    op.create_index("ix_leads_team_source", "leads", ["team_id", "source"])
    op.create_index("ix_leads_team_id", "leads", ["team_id"])
    op.create_index("ix_leads_social_account_id", "leads", ["social_account_id"])
    op.create_index("ix_leads_thread_id", "leads", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_thread_id", table_name="leads")
    op.drop_index("ix_leads_social_account_id", table_name="leads")
    op.drop_index("ix_leads_team_id", table_name="leads")
    op.drop_index("ix_leads_team_source", table_name="leads")
    op.drop_index("ix_leads_team_email", table_name="leads")
    op.drop_index("ix_leads_team_created", table_name="leads")
    op.drop_table("leads")

    op.execute("DROP TYPE IF EXISTS leadcompanysize")
    op.execute("DROP TYPE IF EXISTS leadinterest")
    op.execute("DROP TYPE IF EXISTS leadsource")
