"""LinkedIn newsletter issues — the platform-owned edition queue.

Replaces the shell autopilot (``~/scripts/newsletter-ops/*``): callers
enqueue an issue via ``POST /api/v1/newsletter/issues`` with resolved
content (explicit HTML, ``article_slug``, or ``topic`` generation — same
resolution as ``/newsletter/linkedin/issue``). The
``newsletter-issue-dispatch`` Celery beat claims due rows
(``FOR UPDATE SKIP LOCKED``), publishes through the sidecar→bridge
fallback, and records the result. ``newsletter-issue-verify`` re-checks
published ``article_url`` pages live through the sidecar browser.

Dedup contract: ``slug`` is unique — re-enqueueing the same slug returns
the existing row instead of creating a duplicate edition.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NewsletterIssueStatus(enum.StrEnum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Terminal state for dry_run issues — the editor was driven end-to-end
    # but the final publish click was skipped.
    DRYRUN = "dryrun"


class NewsletterIssue(Base):
    __tablename__ = "newsletter_issues"
    __table_args__ = (
        Index("ix_newsletter_issues_status_scheduled", "status", "scheduled_for"),
        Index("ix_newsletter_issues_verify", "status", "verified_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Dedup key — unique; enqueue is idempotent on slug.
    slug: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # Resolved at enqueue time — dispatch never re-resolves content.
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    newsletter_name: Mapped[str] = mapped_column(String(200), default="The Cloudless Build Log", nullable=False)
    seo_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    post_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # When the dispatch sweep may claim this row (UTC).
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Explicit cover (base64 PNG). NULL + render_cover → render at dispatch.
    cover_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_cover: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[NewsletterIssueStatus] = mapped_column(
        SQLEnum(NewsletterIssueStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=NewsletterIssueStatus.QUEUED,
        nullable=False,
    )
    article_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # Sidecar/bridge step trace + publish flags.
    result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verify_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
