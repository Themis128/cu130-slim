"""LinkedIn Ads campaign snapshots — daily scrape of Campaign Manager metrics,
plus ground-truth report tables imported from Campaign Manager CSV exports."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import UniqueConstraint

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import Team


class AdCampaignSnapshot(Base):
    """Point-in-time metrics for a LinkedIn ad campaign, scraped from Campaign
    Manager via the LinkedIn browser sidecar (the Marketing API ads-reporting
    tier is not granted on this app).

    Appended daily by the ``linkedin-ads-report`` beat task so spend/engagement
    history is kept while a campaign runs.
    """

    __tablename__ = "ad_campaign_snapshots"
    __table_args__ = (
        Index("ix_ad_campaign_snapshots_team_time", "team_id", "captured_at"),
        Index("ix_ad_campaign_snapshots_campaign", "campaign_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="linkedin")
    account_id: Mapped[str] = mapped_column(String(40), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(40), nullable=False)
    campaign_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")

    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    engagement_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cpc_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    budget_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    raw: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    team: Mapped[Team] = relationship("Team")


class AdDailyMetric(Base):
    """Daily-grain ad metrics imported from Campaign Manager report CSVs.

    These rows are the *concept of truth* — exported straight from LinkedIn,
    not scraped estimates. ``report_type`` distinguishes grain:

        campaign_performance  — ad set × day
        creative_performance  — ad × day (ad_id populated)
        lan_* / placements_*  — same grains for audience-network / placement
        conversion_performance / creative_conversion_performance

    ``ad_id`` and ``placement`` are empty strings (never NULL) so the
    uniqueness grain is reliable for upserts.
    """

    __tablename__ = "ad_daily_metrics"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "report_type",
            "campaign_id",
            "ad_set_id",
            "ad_id",
            "placement",
            "day",
            name="uq_ad_daily_metrics_grain",
        ),
        Index("ix_ad_daily_metrics_team_time", "team_id", "day"),
        Index("ix_ad_daily_metrics_ad_set", "ad_set_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="linkedin")
    account_id: Mapped[str] = mapped_column(String(40), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    campaign_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    ad_set_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    ad_set_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    ad_id: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    ad_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    placement: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    day: Mapped[date] = mapped_column(Date, nullable=False)

    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reach: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks_to_landing_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks_to_linkedin_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cpc_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cpm_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    engagement_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    budget_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="report_csv")
    raw: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AdDemographicSegment(Base):
    """Professional-demographics report rows (company, title, seniority, geo …).

    One row per segment value per reporting window — LinkedIn only exports
    windowed aggregates for demographics, not daily data.
    """

    __tablename__ = "ad_demographic_segments"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "segment_type",
            "segment_value",
            "window_start",
            "window_end",
            name="uq_ad_demo_segments_grain",
        ),
        Index("ix_ad_demo_segments_type", "segment_type", "impressions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="linkedin")
    account_id: Mapped[str] = mapped_column(String(40), nullable=False)
    segment_type: Mapped[str] = mapped_column(String(60), nullable=False)
    segment_value: Mapped[str] = mapped_column(String(300), nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)

    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ctr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pct_impressions: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pct_clicks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    source: Mapped[str] = mapped_column(String(40), nullable=False, default="report_csv")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
