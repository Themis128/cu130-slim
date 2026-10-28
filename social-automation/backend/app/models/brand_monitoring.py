"""Brand monitoring models — mentions, competitor snapshots, health scores."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class BrandMention(Base):
    __tablename__ = "brand_mentions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)  # twitter, reddit, google_news, etc.
    author = Column(String(255))  # who mentioned the brand
    content = Column(Text)  # the mention text
    url = Column(Text)  # link to the mention
    sentiment = Column(String(20))  # positive, negative, neutral
    sentiment_score = Column(Float)  # -1.0 to 1.0
    engagement = Column(Integer, default=0)  # likes, retweets, upvotes
    mentioned_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    extra_data = Column(JSONB, default=dict)

    brand = relationship("Brand", back_populates="mentions")


class CompetitorSnapshot(Base):
    __tablename__ = "competitor_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    competitor_name = Column(String(255), nullable=False)
    platform = Column(String(50), nullable=False)
    follower_count = Column(Integer)
    engagement_rate = Column(Float)  # percentage
    post_count = Column(Integer)
    top_post_content = Column(Text)
    top_post_engagement = Column(Integer)
    snapshot_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    extra_data = Column(JSONB, default=dict)

    brand = relationship("Brand", back_populates="competitor_snapshots")
