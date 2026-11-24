"""add talk_rooms table — Nextcloud Talk ↔ messenger bridge room map

Revision ID: t9a8b7c6d5e4
Revises: f7a8b9c0d1e2
Create Date: 2026-11-24 04:30:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t9a8b7c6d5e4"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "talk_rooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("contact_name", sa.String(255), server_default="", nullable=False),
        sa.Column("recipient_id", sa.String(128), server_default="", nullable=False),
        sa.Column("is_e2ee", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("room_token", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_inbound_key", sa.String(256), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_talk_rooms_unique_thread",
        "talk_rooms",
        ["team_id", "platform", "thread_id"],
        unique=True,
    )
    op.create_index("ix_talk_rooms_token", "talk_rooms", ["room_token"], unique=True)
    op.create_index(op.f("ix_talk_rooms_team_id"), "talk_rooms", ["team_id"])


def downgrade() -> None:
    op.drop_table("talk_rooms")
