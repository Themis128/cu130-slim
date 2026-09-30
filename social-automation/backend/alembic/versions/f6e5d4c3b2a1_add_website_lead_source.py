"""add 'website' to leadsource enum

Public newsletter/lead capture on the marketing site stores leads with
source=website (public /api/v1/leads/public endpoint).

Revision ID: f6e5d4c3b2a1
Revises: c8d9e0f1a2b3
Create Date: 2026-09-30 13:30:00.000000

"""
from alembic import op

revision = "f6e5d4c3b2a1"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE leadsource ADD VALUE IF NOT EXISTS 'website'")


def downgrade() -> None:
    # Postgres cannot drop enum values — rebuild the type instead.
    op.execute(
        """
        ALTER TYPE leadsource RENAME TO leadsource_old;
        CREATE TYPE leadsource AS ENUM (
            'whatsapp_flow','whatsapp_dm','facebook_messenger','instagram_dm'
        );
        ALTER TABLE leads ALTER COLUMN source TYPE leadsource
            USING source::text::leadsource;
        DROP TYPE leadsource_old;
        """
    )
