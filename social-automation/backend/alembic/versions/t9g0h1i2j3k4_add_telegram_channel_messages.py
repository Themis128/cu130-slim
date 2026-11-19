"""add telegram_channel_messages archive table

Canonical read-side store for the Cloudless Telegram infra channels.
Populated by the host-side Telethon archiver (scripts/telegram_archive.py)
which dual-writes SQLite + Postgres; the Bot API cannot read history.

Revision ID: t9g0h1i2j3k4
Revises: d4e5f6a7b8c9
Create Date: 2026-11-19 12:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "t9g0h1i2j3k4"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "telegram_channel_messages" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "telegram_channel_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sender", sa.String(255), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("media", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel", "msg_id", name="uq_tg_msg_channel_msgid"),
    )
    op.create_index("ix_telegram_channel_messages_channel", "telegram_channel_messages", ["channel"])
    op.create_index("ix_telegram_channel_messages_date", "telegram_channel_messages", ["date"])
    op.create_index("ix_telegram_channel_messages_team_id", "telegram_channel_messages", ["team_id"])


def downgrade() -> None:
    op.drop_table("telegram_channel_messages")
