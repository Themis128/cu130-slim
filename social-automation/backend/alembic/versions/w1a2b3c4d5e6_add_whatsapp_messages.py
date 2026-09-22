"""add whatsapp_messages table

Persists inbound/outbound WhatsApp Cloud API messages so the unified inbox
can list WhatsApp conversations (the Cloud API has no list-conversations
endpoint).

Revision ID: a1b2c3d4e5f6
Revises: z9k0l1m2n3o4
Create Date: 2026-09-22 20:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "w1a2b3c4d5e6"
down_revision = "z9k0l1m2n3o4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_number_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("sender_phone", sa.String(32), nullable=False),
        sa.Column("sender_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("direction", sa.String(16), nullable=False, server_default="inbound"),
        sa.Column("message_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("message_type", sa.String(32), nullable=False, server_default="text"),
        sa.Column("message_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("social_account_id", "message_id", name="uq_wa_msg_account_wamid"),
    )
    op.create_index("ix_whatsapp_messages_team_id", "whatsapp_messages", ["team_id"])
    op.create_index("ix_whatsapp_messages_social_account_id", "whatsapp_messages", ["social_account_id"])
    op.create_index("ix_whatsapp_messages_sender_phone", "whatsapp_messages", ["sender_phone"])
    op.create_index("ix_whatsapp_messages_direction", "whatsapp_messages", ["direction"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_messages_direction", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_sender_phone", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_social_account_id", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_team_id", table_name="whatsapp_messages")
    op.drop_table("whatsapp_messages")
