"""Inbox snapshot — point-in-time capture of unified-inbox conversations.

The unified inbox (``app.api.inbox``) aggregates live provider APIs with no
persistence, so unanswered-DM counts and response-time trends would be
invisible to reports. A Celery beat captures the normalized conversation
list every few hours; the daily lead-pulse report reads the latest batch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InboxSnapshot(Base):
    __tablename__ = "inbox_snapshots"
    __table_args__ = (
        Index("ix_inbox_snapshots_team_time", "team_id", "captured_at"),
        Index("ix_inbox_snapshots_thread", "platform", "thread_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True)
    # One capture run shares a batch id so reports can isolate a snapshot.
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    account_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    account_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    thread_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    e2ee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Last-activity timestamp reported by the platform (ISO string kept raw —
    # formats differ per provider; parsed lazily by consumers).
    last_activity_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
