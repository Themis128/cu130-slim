import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PostStatus(enum.StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_team_status", "team_id", "status"),
        # Partial index created via migration to avoid enum creation order issues
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[PostStatus] = mapped_column(
        SQLEnum(PostStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=PostStatus.DRAFT,
        nullable=False,
        index=True,
    )
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=[], nullable=False)
    platform_specific: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)  # per-platform overrides
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    mention_accounts: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    link_preview_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="posts")
    author: Mapped["User"] = relationship("User", back_populates="posts")
    targets: Mapped[list["PostTarget"]] = relationship("PostTarget", back_populates="post", cascade="all, delete-orphan")


class MediaCollection(Base):
    __tablename__ = "media_collections"
    __table_args__ = (
        Index("ix_media_collections_team", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="media_collections")
    creator: Mapped["User"] = relationship("User", back_populates="media_collections")
    assets: Mapped[list["MediaAsset"]] = relationship("MediaAsset", back_populates="collection")


class StorageBackend(enum.StrEnum):
    local = "local"
    r2 = "r2"


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_team", "team_id"),
        Index("ix_media_assets_collection", "collection_id"),
        Index("ix_media_assets_backend", "storage_backend"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_collections.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    storage_backend: Mapped[StorageBackend] = mapped_column(
        SQLEnum(StorageBackend, values_callable=lambda obj: [e.value for e in obj]),
        default=StorageBackend.local,
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)  # relative path for local, R2 key for r2
    public_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(nullable=True)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    ai_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    ai_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="upload", nullable=False)  # upload, url, ai-generated
    generation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    comfyui_workflow_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embedding_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_favorite: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    usage_count: Mapped[int] = mapped_column(default=0, nullable=False)
    meta_data: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="media_assets")
    uploader: Mapped["User"] = relationship("User", back_populates="media_assets")
    collection: Mapped["MediaCollection"] = relationship("MediaCollection", back_populates="assets")


class PostTarget(Base):
    __tablename__ = "post_targets"

    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), primary_key=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, published, failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    post: Mapped["Post"] = relationship("Post", back_populates="targets")
    social_account: Mapped["SocialAccount"] = relationship("SocialAccount", back_populates="post_targets")
