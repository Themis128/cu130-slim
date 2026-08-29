"""Daily SocialAuto digest for Slack (#socialauto).

Collects analytics + operational issues and posts via Incoming Webhook
(`SLACK_WEBHOOK_URL`) or Slack Web API (`SLACK_BOT_TOKEN` + channel).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.analytics import PostAnalyticsSnapshot
from app.models.content import Post, PostStatus
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.models.user import Team

logger = logging.getLogger(__name__)


@dataclass
class DigestIssue:
    severity: str  # error | warning
    title: str
    detail: str = ""


@dataclass
class DigestReport:
    generated_at: datetime
    timezone: str
    team_name: str
    days: int
    overview: dict[str, Any] = field(default_factory=dict)
    impressions_24h: int = 0
    engagement_24h: int = 0
    top_posts: list[dict[str, Any]] = field(default_factory=list)
    issues: list[DigestIssue] = field(default_factory=list)
    posted_to_slack: bool = False
    slack_error: str | None = None
    emailed: bool = False
    email_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "timezone": self.timezone,
            "team_name": self.team_name,
            "days": self.days,
            "overview": self.overview,
            "impressions_24h": self.impressions_24h,
            "engagement_24h": self.engagement_24h,
            "top_posts": self.top_posts,
            "issues": [
                {"severity": i.severity, "title": i.title, "detail": i.detail}
                for i in self.issues
            ],
            "posted_to_slack": self.posted_to_slack,
            "slack_error": self.slack_error,
            "emailed": self.emailed,
            "email_error": self.email_error,
            "markdown": self.to_slack_markdown(),
        }

    def to_slack_markdown(self) -> str:
        tz = ZoneInfo(self.timezone)
        when = self.generated_at.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z")
        o = self.overview
        lines = [
            f"*SocialAuto daily report* · {self.team_name}",
            f"_{when}_ · last {self.days} days",
            "",
            "*Analytics*",
            f"• Posts: *{o.get('total_posts', 0)}* total · "
            f"*{o.get('published_posts', 0)}* published · "
            f"*{o.get('scheduled_posts', 0)}* scheduled · "
            f"*{o.get('draft_posts', 0)}* drafts · "
            f"*{o.get('failed_posts', 0)}* failed",
            f"• Engagement (period): *{o.get('total_engagement', 0)}*",
            f"• Last 24h snapshots: impressions *{self.impressions_24h}* · "
            f"engagement *{self.engagement_24h}*",
            f"• Connected accounts: *{o.get('connected_accounts', 0)}*",
        ]
        if self.top_posts:
            lines.append("")
            lines.append("*Top posts (by engagement)*")
            for i, p in enumerate(self.top_posts[:5], 1):
                snippet = (p.get("snippet") or "").replace("\n", " ")[:80]
                lines.append(
                    f"{i}. eng *{p.get('engagement', 0)}* · "
                    f"imp *{p.get('impressions', 0)}* — {snippet or p.get('post_id')}"
                )

        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        lines.append("")
        if not errors and not warnings:
            lines.append("*Issues:* none 🟢")
        else:
            if errors:
                lines.append(f"*Errors ({len(errors)})*")
                for issue in errors[:8]:
                    detail = f" — {issue.detail}" if issue.detail else ""
                    lines.append(f"• ❌ *{issue.title}*{detail}")
            if warnings:
                lines.append(f"*Warnings ({len(warnings)})*")
                for issue in warnings[:8]:
                    detail = f" — {issue.detail}" if issue.detail else ""
                    lines.append(f"• ⚠️ *{issue.title}*{detail}")

        lines.append("")
        lines.append("_Channel: #socialauto · cloudless.gr Social Automation_")
        return "\n".join(lines)


async def build_daily_digest(
    db: AsyncSession,
    *,
    team: Team,
    days: int = 1,
) -> DigestReport:
    """Build analytics + issues digest for one team (default: last 24h window metrics)."""
    settings = get_settings()
    tz_name = settings.APP_TIMEZONE or "Europe/Athens"
    now = datetime.now(UTC)
    since = now - timedelta(days=max(1, days))
    since_24h = now - timedelta(hours=24)

    # Post status counts for period
    post_counts = await db.execute(
        select(Post.status, func.count(Post.id))
        .where(Post.team_id == team.id, Post.created_at >= since)
        .group_by(Post.status)
    )
    counts = {status: count for status, count in post_counts.all()}

    accounts_count = await db.execute(
        select(func.count(SocialAccount.id)).where(
            SocialAccount.team_id == team.id,
            SocialAccount.status == "active",
        )
    )

    # Latest snapshots engagement / impressions (24h capture window)
    snap_rows = await db.execute(
        select(
            func.coalesce(func.sum(PostAnalyticsSnapshot.impressions), 0),
            func.coalesce(func.sum(PostAnalyticsSnapshot.engagement), 0),
        ).where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since_24h,
        )
    )
    impressions_24h, engagement_24h = snap_rows.one()
    impressions_24h = int(impressions_24h or 0)
    engagement_24h = int(engagement_24h or 0)

    # Top posts: latest snapshot per platform_post_id, ranked by engagement
    latest_snap = (
        select(
            PostAnalyticsSnapshot.id,
            func.row_number()
            .over(
                partition_by=PostAnalyticsSnapshot.platform_post_id,
                order_by=PostAnalyticsSnapshot.captured_at.desc(),
            )
            .label("rn"),
        )
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since,
            PostAnalyticsSnapshot.platform_post_id.isnot(None),
        )
        .subquery()
    )
    top_q = await db.execute(
        select(
            PostAnalyticsSnapshot.post_id,
            PostAnalyticsSnapshot.platform_post_id,
            PostAnalyticsSnapshot.impressions,
            PostAnalyticsSnapshot.engagement,
            Post.content_text,
        )
        .join(latest_snap, latest_snap.c.id == PostAnalyticsSnapshot.id)
        .outerjoin(Post, Post.id == PostAnalyticsSnapshot.post_id)
        .where(latest_snap.c.rn == 1)
        .order_by(PostAnalyticsSnapshot.engagement.desc().nullslast())
        .limit(5)
    )
    top_posts: list[dict[str, Any]] = []
    for post_id, platform_post_id, imps, eng, text in top_q.all():
        snippet = (text or "").strip()
        if not snippet and platform_post_id:
            snippet = f"post {platform_post_id[-12:]}"
        top_posts.append(
            {
                "post_id": str(post_id) if post_id else None,
                "platform_post_id": platform_post_id,
                "impressions": int(imps or 0),
                "engagement": int(eng or 0),
                "snippet": snippet[:120],
            }
        )

    overview = {
        "total_posts": sum(counts.values()),
        "published_posts": counts.get(PostStatus.PUBLISHED, 0),
        "scheduled_posts": counts.get(PostStatus.SCHEDULED, 0),
        "draft_posts": counts.get(PostStatus.DRAFT, 0),
        "failed_posts": counts.get(PostStatus.FAILED, 0),
        "connected_accounts": int(accounts_count.scalar() or 0),
        "total_engagement": engagement_24h,
    }

    issues: list[DigestIssue] = []

    # Failed posts (24h)
    failed_posts = await db.execute(
        select(Post)
        .where(
            Post.team_id == team.id,
            Post.status == PostStatus.FAILED,
            Post.updated_at >= since_24h,
        )
        .order_by(Post.updated_at.desc())
        .limit(10)
    )
    for post in failed_posts.scalars().all():
        issues.append(
            DigestIssue(
                severity="error",
                title=f"Post failed ({str(post.id)[:8]})",
                detail=(post.error_message or (post.content_text or "")[:100])[:200],
            )
        )

    # Failed publish queue (24h)
    failed_q = await db.execute(
        select(PublishQueue)
        .join(Post, Post.id == PublishQueue.post_id)
        .where(
            Post.team_id == team.id,
            PublishQueue.status == QueueStatus.FAILED,
            PublishQueue.created_at >= since_24h,
        )
        .order_by(PublishQueue.created_at.desc())
        .limit(10)
    )
    for item in failed_q.scalars().all():
        issues.append(
            DigestIssue(
                severity="error",
                title=f"Publish queue failed ({str(item.id)[:8]})",
                detail=f"attempts={item.attempts}/{item.max_attempts}",
            )
        )

    # Snapshot notes that look like errors (24h)
    bad_snaps = await db.execute(
        select(PostAnalyticsSnapshot)
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since_24h,
            PostAnalyticsSnapshot.notes.isnot(None),
        )
        .order_by(PostAnalyticsSnapshot.captured_at.desc())
        .limit(30)
    )
    for snap in bad_snaps.scalars().all():
        note = (snap.notes or "").lower()
        detail = (snap.notes or "")[:200]
        # Soft LinkedIn misses (stale ids / no activity) are expected; skip.
        if "activityids" in note or note.startswith("stats_unavailable"):
            continue
        if any(k in note for k in ("http 5", "denied", "quota", "unauthorized", "forbidden")):
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Analytics sync warning",
                    detail=detail,
                )
            )
            continue
        if any(k in note for k in ("error", "http 4", "fail")):
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Analytics sync warning",
                    detail=detail,
                )
            )

    # Dedupe identical digest issues
    deduped: list[DigestIssue] = []
    seen_issue: set[str] = set()
    for issue in issues:
        key = f"{issue.severity}|{issue.title}|{issue.detail}"
        if key in seen_issue:
            continue
        seen_issue.add(key)
        deduped.append(issue)
    issues = deduped

    # No LinkedIn/org account connected
    if overview["connected_accounts"] == 0:
        issues.append(
            DigestIssue(
                severity="warning",
                title="No active social accounts",
                detail="Connect LinkedIn Company Page to publish and sync analytics",
            )
        )

    # High failed rate
    if overview["failed_posts"] and overview["total_posts"]:
        rate = overview["failed_posts"] / max(overview["total_posts"], 1)
        if rate >= 0.2:
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Elevated post failure rate",
                    detail=f"{overview['failed_posts']}/{overview['total_posts']} failed in window",
                )
            )

    return DigestReport(
        generated_at=now,
        timezone=tz_name,
        team_name=getattr(team, "name", None) or "Cloudless",
        days=days,
        overview=overview,
        impressions_24h=impressions_24h,
        engagement_24h=engagement_24h,
        top_posts=top_posts,
        issues=issues,
    )


async def post_digest_to_slack(report: DigestReport) -> DigestReport:
    """Send digest markdown to Slack. Prefers webhook, then bot/access token."""
    settings = get_settings()
    text = report.to_slack_markdown()
    webhook = (settings.SLACK_WEBHOOK_URL or "").strip()
    token = (
        (settings.SLACK_BOT_TOKEN or "").strip()
        or (settings.SLACK_ACCESS_TOKEN or "").strip()
    )
    channel = (settings.SLACK_CHANNEL_ID or "").strip() or "C0BT263L17U"  # #socialauto

    if not webhook and not token:
        report.slack_error = (
            "Slack not configured. Set SLACK_WEBHOOK_URL (Incoming Webhook for #socialauto) "
            "or SLACK_BOT_TOKEN / SLACK_ACCESS_TOKEN + SLACK_CHANNEL_ID in .env"
        )
        logger.warning(report.slack_error)
        return report

    last_err: str | None = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(1, 5):
                try:
                    if webhook:
                        resp = await client.post(webhook, json={"text": text})
                        if resp.status_code >= 300:
                            last_err = f"Webhook HTTP {resp.status_code}: {resp.text[:200]}"
                            # webhooks rarely need retry on 4xx
                            if resp.status_code < 500:
                                report.slack_error = last_err
                                return report
                        else:
                            report.posted_to_slack = True
                            return report
                    else:
                        # Prefer api.slack.com — bare slack.com TLS often hangs in Docker/WSL
                        resp = await client.post(
                            "https://api.slack.com/api/chat.postMessage",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"channel": channel, "text": text, "mrkdwn": True},
                        )
                        data = resp.json()
                        if not data.get("ok"):
                            err = data.get("error") or "unknown"
                            needed = data.get("needed")
                            detail = f"Slack API error: {err}"
                            if needed:
                                detail += f" (needed: {needed})"
                            report.slack_error = detail
                            return report
                        report.posted_to_slack = True
                        return report
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                    last_err = f"{type(exc).__name__}: {exc or repr(exc)}"
                    logger.warning("Slack post attempt %s failed: %s", attempt, last_err)
                    if attempt < 4:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    report.slack_error = last_err
                    return report
    except Exception as exc:  # noqa: BLE001
        report.slack_error = str(exc) or repr(exc)
        logger.exception("Failed to post Slack digest")
    if last_err and not report.posted_to_slack and not report.slack_error:
        report.slack_error = last_err
    return report


async def run_daily_digest_for_all_teams(
    db: AsyncSession,
    *,
    days: int = 1,
    post_to_slack: bool = True,
    post_to_email: bool = True,
) -> list[dict[str, Any]]:
    from app.services.email_digest import email_digest

    teams = (await db.execute(select(Team))).scalars().all()
    results: list[dict[str, Any]] = []
    for team in teams:
        report = await build_daily_digest(db, team=team, days=days)
        # Skip empty/test teams when posting (still include in API preview)
        active = (
            report.overview.get("connected_accounts", 0) > 0
            or report.overview.get("total_posts", 0) > 0
            or report.impressions_24h > 0
        )
        if active:
            if post_to_slack:
                try:
                    report = await post_digest_to_slack(report)
                except Exception as exc:  # noqa: BLE001
                    report.slack_error = str(exc) or repr(exc)
            if post_to_email:
                try:
                    report = await email_digest(report)
                except Exception as exc:  # noqa: BLE001
                    report.email_error = str(exc) or repr(exc)
        else:
            if post_to_slack:
                report.slack_error = "skipped empty team"
            if post_to_email:
                report.email_error = "skipped empty team"
        results.append(report.to_dict())
    return results
