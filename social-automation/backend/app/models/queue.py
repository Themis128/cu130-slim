import uuid
from datetime import datetime, UTC
from sqlalchemy import String, DateTime, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base
import enum


class QueueStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PublishQueue(Base):
    __tablename__ = "publish_queue"
    __table_args__ = (
        Index("ix_publish_queue_scheduled_status", "scheduled_at", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    status: Mapped[QueueStatus] = mapped_column(SQLEnum(QueueStatus), default=QueueStatus.PENDING, nullable=False, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    post: Mapped["Post"] = relationship("Post")
    social_account: Mapped["SocialAccount"] = relationship("SocialAccount")