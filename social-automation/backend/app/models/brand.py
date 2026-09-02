"""Brand identity models for the AI branding platform.

Each team has at most one Brand. A Brand contains:
- Brand DNA (positioning, mission, values, audience)
- BrandVoice (tone dimensions, messaging pillars, banned/preferred phrases)
- BrandVisual (colors, fonts, logo, image style)
- BrandGuidelines (compiled, shareable document)
- BrandAsset (logos, templates, OG images, favicons stored in media library)
"""
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BrandAssetType(enum.StrEnum):
    logo = "logo"
    logo_dark = "logo_dark"
    logo_light = "logo_light"
    logo_monochrome = "logo_monochrome"
    social_template = "social_template"
    og_image = "og_image"
    favicon = "favicon"
    email_header = "email_header"
    business_card = "business_card"
    cover_photo = "cover_photo"
    other = "other"


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (
        Index("ix_brands_team", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    positioning_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    values: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    target_audience: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    competitor_names: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(300), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", backref="brand")
    voice: Mapped["BrandVoice | None"] = relationship("BrandVoice", back_populates="brand", uselist=False, cascade="all, delete-orphan")
    visual: Mapped["BrandVisual | None"] = relationship("BrandVisual", back_populates="brand", uselist=False, cascade="all, delete-orphan")
    guidelines: Mapped["BrandGuidelines | None"] = relationship("BrandGuidelines", back_populates="brand", uselist=False, cascade="all, delete-orphan")
    assets: Mapped[list["BrandAsset"]] = relationship("BrandAsset", back_populates="brand", cascade="all, delete-orphan")
    mentions: Mapped[list["BrandMention"]] = relationship("BrandMention", back_populates="brand", cascade="all, delete-orphan")
    competitor_snapshots: Mapped[list["CompetitorSnapshot"]] = relationship("CompetitorSnapshot", back_populates="brand", cascade="all, delete-orphan")


class BrandVoice(Base):
    __tablename__ = "brand_voices"
    __table_args__ = (
        Index("ix_brand_voices_brand", "brand_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, unique=True)
    tone_dimensions: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    messaging_pillars: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    banned_phrases: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    preferred_phrases: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    example_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_signature: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    brand: Mapped["Brand"] = relationship("Brand", back_populates="voice")


class BrandVisual(Base):
    __tablename__ = "brand_visuals"
    __table_args__ = (
        Index("ix_brand_visuals_brand", "brand_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, unique=True)
    primary_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    neutral_colors: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    font_heading: Mapped[str | None] = mapped_column(String(200), nullable=True)
    font_body: Mapped[str | None] = mapped_column(String(200), nullable=True)
    type_scale: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_variants: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    image_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    photography_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    brand: Mapped["Brand"] = relationship("Brand", back_populates="visual")


class BrandGuidelines(Base):
    __tablename__ = "brand_guidelines"
    __table_args__ = (
        Index("ix_brand_guidelines_brand", "brand_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, unique=True)
    content: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    brand: Mapped["Brand"] = relationship("Brand", back_populates="guidelines")


class BrandAsset(Base):
    __tablename__ = "brand_assets"
    __table_args__ = (
        Index("ix_brand_assets_brand", "brand_id"),
        Index("ix_brand_assets_type", "asset_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    asset_type: Mapped[BrandAssetType] = mapped_column(
        SQLEnum(BrandAssetType, values_callable=lambda obj: [e.value for e in obj]),
        default=BrandAssetType.other,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_metadata: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    brand: Mapped["Brand"] = relationship("Brand", back_populates="assets")
