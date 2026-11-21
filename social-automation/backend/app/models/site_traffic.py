"""Daily site-traffic rollups pulled from self-hosted Swetrix CE.

Swetrix (analytics.cloudless.gr) is the cookieless top-of-funnel truth for
owned web properties — it sees every visitor, unlike the consent-gated
``web_analytics_events`` webhook stream. ``swetrix_sync`` upserts one row
per (team, domain, date) so the datalake export and reports can join site
traffic against posts/leads on UTM parameters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteTrafficDaily(Base):
    """One row per project per day: Swetrix traffic aggregates + top-N snapshot."""

    __tablename__ = "site_traffic_daily"
    __table_args__ = (
        UniqueConstraint("team_id", "domain", "date", name="uq_site_traffic_daily"),
        Index("ix_site_traffic_daily_team_date", "team_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    swetrix_pid: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    visitors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pageviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bounce_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    session_duration_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Latest breakdown snapshot for the most recent synced day:
    # {"top_pages": [...], "top_countries": [...], "top_referrers": [...],
    #  "utm_sources": [...]}
    meta: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
