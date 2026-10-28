"""Digital Business Card model — vCard 4.0 (RFC 6350) compliant cards with share tokens."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DigitalCard(Base):
    """A digital business card linked to a team's brand.

    Stores contact info, social links, and a share token for public access.
    Generates vCard 4.0 compliant VCF files for contact import.
    """

    __tablename__ = "digital_cards"
    __table_args__ = (
        Index("ix_digital_cards_team", "team_id"),
        Index("ix_digital_cards_token", "share_token", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)

    # Card identity
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # Full name (FN)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Job title
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)  # Organization
    tagline: Mapped[str | None] = mapped_column(String(300), nullable=True)  # Short tagline
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # Bio / about

    # Contact info
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Visual identity (from brand or custom)
    primary_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Social links as JSON: [{platform, handle, url, icon}]
    social_links: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)

    # Messaging pillars / services as JSON: [{title, description}]
    services: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)

    # Sharing
    share_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # Analytics
    view_count: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    contact_save_count: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    share_count: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)

    # Metadata
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", backref="digital_cards")
    brand: Mapped["Brand | None"] = relationship("Brand", backref="digital_cards")
