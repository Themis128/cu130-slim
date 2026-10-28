"""WhatsApp message model — persists inbound/outbound Cloud API messages.

The WhatsApp Cloud API has no "list conversations" endpoint, so the unified
inbox builds WhatsApp threads from messages stored here as they arrive
(webhook) or are sent (send endpoints, auto-reply).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        UniqueConstraint("social_account_id", "message_id", name="uq_wa_msg_account_wamid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    phone_number_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Remote party phone number (E.164 digits) — the conversation peer for
    # both inbound and outbound rows.
    sender_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="inbound", index=True)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    message_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
