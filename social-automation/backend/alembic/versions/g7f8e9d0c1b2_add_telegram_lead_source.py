"""add 'telegram_dm' to leadsource enum

Telegram bot DMs run the same lead-capture FSM as Messenger/Instagram —
channel-post CTA deep-links (t.me/<bot>?start=audit) land here.

Revision ID: g7f8e9d0c1b2
Revises: f6e5d4c3b2a1
Create Date: 2026-10-13 09:00:00.000000

"""

from alembic import op

revision = "g7f8e9d0c1b2"
down_revision = "f6e5d4c3b2a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE leadsource ADD VALUE IF NOT EXISTS 'telegram_dm'")


def downgrade() -> None:
    # Postgres cannot drop enum values — rebuild the type instead.
    # Existing telegram_dm rows would fail the cast, so re-tag them as
    # generic website leads first (keeps the lead, loses only the source).
    op.execute(
        """
        UPDATE leads SET source = 'website' WHERE source = 'telegram_dm';
        ALTER TYPE leadsource RENAME TO leadsource_old;
        CREATE TYPE leadsource AS ENUM (
            'whatsapp_flow','whatsapp_dm','facebook_messenger','instagram_dm','website'
        );
        ALTER TABLE leads ALTER COLUMN source TYPE leadsource
            USING source::text::leadsource;
        DROP TYPE leadsource_old;
        """
    )
