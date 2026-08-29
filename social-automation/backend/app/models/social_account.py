import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("team_id", "platform", "account_id", name="uq_social_account"),
        Index("ix_social_accounts_team_platform", "team_id", "platform"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)  # linkedin, twitter, instagram, facebook, threads
    account_id: Mapped[str] = mapped_column(String(100), nullable=False)  # platform's user ID
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_enc: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active, expired, revoked, error
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="social_accounts")
    post_targets: Mapped[list["PostTarget"]] = relationship("PostTarget", back_populates="social_account", cascade="all, delete-orphan")
