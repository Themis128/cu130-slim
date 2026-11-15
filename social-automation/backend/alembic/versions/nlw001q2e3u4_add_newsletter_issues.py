"""add newsletter_issues table (platform-owned LinkedIn newsletter queue)

Replaces the ~/scripts/newsletter-ops shell autopilot: issues are enqueued
via POST /api/v1/newsletter/issues, claimed by the newsletter-issue-dispatch
Celery beat (FOR UPDATE SKIP LOCKED), published through the sidecar→bridge
fallback, and verified live by newsletter-issue-verify.

Guarded by has_table — environments that already created the table
manually skip creation (same pattern as ops_incidents).

Revision ID: a1b2c3d4e5f6
Revises: c9d8e7f6a5b4
Create Date: 2026-11-15 09:30:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "nlw001q2e3u4"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None

_ISSUE_STATUS = ("queued", "publishing", "published", "failed", "cancelled", "dryrun")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("newsletter_issues"):
        return
    op.create_table(
        "newsletter_issues",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(220), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("newsletter_name", sa.String(200), nullable=False, server_default="The Cloudless Build Log"),
        sa.Column("seo_title", sa.String(300), nullable=True),
        sa.Column("seo_description", sa.String(500), nullable=True),
        sa.Column("post_description", sa.Text(), nullable=True),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cover_base64", sa.Text(), nullable=True),
        sa.Column("render_cover", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "status",
            sa.Enum(*_ISSUE_STATUS, name="newsletterissuestatus"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("article_url", sa.String(600), nullable=True),
        sa.Column("result", JSONB, nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verify_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_newsletter_issues_status_scheduled", "newsletter_issues", ["status", "scheduled_for"])
    op.create_index("ix_newsletter_issues_verify", "newsletter_issues", ["status", "verified_at"])


def downgrade() -> None:
    op.drop_table("newsletter_issues")
