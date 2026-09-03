import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PostStatus(enum.StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class RecurrencePattern(enum.StrEnum):
    """How a recurring post repeats after each successful publish."""
    NONE = "none"          # one-off post (default)
    DAILY = "daily"        # repeat every N days
    WEEKLY = "weekly"      # repeat every N weeks
    MONTHLY = "monthly"    # repeat every N months


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
    music_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pillars.id", ondelete="SET NULL"), nullable=True, index=True)
    content_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_briefs.id", ondelete="SET NULL"), nullable=True, index=True)
    # ── Recurring post support ────────────────────────────────────────────
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recurrence_pattern: Mapped[RecurrencePattern] = mapped_column(
        SQLEnum(RecurrencePattern, values_callable=lambda obj: [e.value for e in obj]),
        default=RecurrencePattern.NONE,
        nullable=False,
    )
    recurrence_interval: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # N days/weeks/months
    recurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # how many times repeated so far
    recurrence_max: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = unlimited
    recurrence_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True)
    next_recurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team", back_populates="posts")
    author: Mapped["User"] = relationship("User", back_populates="posts")
    targets: Mapped[list["PostTarget"]] = relationship("PostTarget", back_populates="post", cascade="all, delete-orphan")
    comments: Mapped[list["PostComment"]] = relationship("PostComment", back_populates="post", cascade="all, delete-orphan", order_by="PostComment.created_at")


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
    minio = "minio"


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


class PostComment(Base):
    """Approval workflow comments — preserved through status transitions."""

    __tablename__ = "post_comments"
    __table_args__ = (
        Index("ix_post_comments_post", "post_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)  # submit_review, approve, reject, comment
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    post: Mapped["Post"] = relationship("Post", back_populates="comments")


class Pillar(Base):
    """Content pillar — one of the strategic themes posts are organized around."""

    __tablename__ = "pillars"
    __table_args__ = (
        Index("ix_pillars_team", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String(20), default="#6366f1", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team")


class ContentBrief(Base):
    """Content brief — a structured prompt/outline linked to a pillar."""

    __tablename__ = "content_briefs"
    __table_args__ = (
        Index("ix_content_briefs_team_pillar", "team_id", "pillar_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pillars.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    outline: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_platforms: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    tone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    team: Mapped["Team"] = relationship("Team")
    pillar: Mapped["Pillar | None"] = relationship("Pillar")
