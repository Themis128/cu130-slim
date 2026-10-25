"""Outbound engagement actions — queue + audit trail."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EngagementAction(Base):
    __tablename__ = "engagement_actions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(20))
    target_post_id: Mapped[str] = mapped_column(String(120))
    target_author: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(20))  # like|repost|comment
    score: Mapped[float] = mapped_column(Float)
    jitter_seconds: Mapped[int] = mapped_column(Integer, default=0)
    comment_draft: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EngagementCandidate(Base):
    __tablename__ = "engagement_candidates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(20))
    author_handle: Mapped[str] = mapped_column(String(120))
    author_name: Mapped[str] = mapped_column(String(120), default="")
    post_id: Mapped[str] = mapped_column(String(120), unique=True)
    post_text: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    connection_tier: Mapped[str] = mapped_column(String(10), default="medium")
    is_followed: Mapped[bool] = mapped_column(default=True)
    is_connection: Mapped[bool] = mapped_column(default=False)
    in_crm: Mapped[bool] = mapped_column(default=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    scored: Mapped[bool] = mapped_column(default=False)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
