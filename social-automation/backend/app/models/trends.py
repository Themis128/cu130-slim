"""Persisted trend signals — the datalake layer for trendwatch feeds.

Google Trends (daily) and TikTok Creative Center (weekly) signals are
fetched by ``app.worker.tasks.trend_ingest``, scored through the niche
matrix, and stored here deduped by (source, query, geo, period_key) so
Metabase/datalake consumers can join trend history against posts,
engagement, and conversions.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrendSignal(Base):
    __tablename__ = "trend_signals"
    __table_args__ = (UniqueConstraint("source", "query", "geo", "period_key", name="uq_trend_signal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(30))  # google-trends | tiktok-cc
    query: Mapped[str] = mapped_column(Text)
    geo: Mapped[str] = mapped_column(String(10))
    traffic: Mapped[int] = mapped_column(BigInteger, default=0)
    classification: Mapped[str] = mapped_column(String(20))  # opportunity|early_window|noise
    swot: Mapped[str] = mapped_column(String(5), default="")
    # "2026-11-08" for daily feeds, "2026-11-09" week-ending for TikTok weekly
    period_key: Mapped[str] = mapped_column(String(20))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
