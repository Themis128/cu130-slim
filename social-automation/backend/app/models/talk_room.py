"""Talk bridge room map — one Nextcloud Talk room per external conversation.

Each row binds a platform DM thread (FB Page Messenger, FB personal
Messenger, Instagram DM, LinkedIn DM) to a Nextcloud Talk group room.
Inbound platform messages are mirrored into the room; user replies in
the room are routed back to the platform thread.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TalkRoom(Base):
    __tablename__ = "talk_rooms"
    __table_args__ = (
        Index("ix_talk_rooms_unique_thread", "team_id", "platform", "thread_id", unique=True),
        Index("ix_talk_rooms_token", "room_token", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # External side
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # messenger|personal_messenger|instagram|linkedin
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)  # social_accounts.id
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)  # conversation/thread id
    contact_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)  # psid / ig-user-id / li thread
    is_e2ee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Talk side
    room_token: Mapped[str] = mapped_column(String(16), nullable=False)

    # Sync state
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_inbound_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
