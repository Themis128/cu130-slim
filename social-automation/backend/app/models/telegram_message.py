"""Telegram channel message archive — read-side store for the Cloudless
infra alert channels (#critical/#ops/#intel/#digest).

The Bot API can post but cannot read channel history, so a Telethon
user-session archiver (scripts/telegram_archive.py, hourly cron) upserts
rows here. Writes still go through @Cloudless_newbot — this table is the
canonical searchable history for triage and audits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramChannelMessage(Base):
    __tablename__ = "telegram_channel_messages"
    __table_args__ = (
        UniqueConstraint("channel", "msg_id", name="uq_tg_msg_channel_msgid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Logical channel name from TG_CH_* mapping: critical/ops/intel/digest.
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Telegram message id within the channel (monotonic per channel).
    msg_id: Mapped[int] = mapped_column(nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Media type name when the message carried an attachment (no bytes stored).
    media: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
