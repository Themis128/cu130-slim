"""telegram_channel_messages.msg_id -> BIGINT

Slack/ntfy collectors write message ids derived from high-precision
timestamps (Slack ts 'epoch.microseconds' → 16 digits) which overflow
int4. Telegram ids stay small; bigint fits every surface.

Revision ID: u0a1b2c3d4e5
Revises: t9g0h1i2j3k4
Create Date: 2026-11-19 14:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "u0a1b2c3d4e5"
down_revision = "t9g0h1i2j3k4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "telegram_channel_messages",
        "msg_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
    )


def downgrade() -> None:
    op.alter_column(
        "telegram_channel_messages",
        "msg_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
    )
