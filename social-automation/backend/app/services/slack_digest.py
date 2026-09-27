"""Daily SocialAuto digest for Slack (#socialauto).

Collects analytics + operational issues and posts via Incoming Webhook
(`SLACK_WEBHOOK_URL`) or Slack Web API (`SLACK_BOT_TOKEN` + channel).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.ai_usage import AIUsageLog
from app.models.analytics import (
    AnalyticsEvent,
    FollowerSnapshot,
    PostAnalyticsSnapshot,
)
from app.models.content import Post, PostStatus
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.services.slack_notifications import (
    post_alert_to_slack,
    post_digest_text_to_slack,
    post_thread_reply,
)

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
    growth: dict[str, Any] = field(default_factory=dict)
    issues: list[DigestIssue] = field(default_factory=list)
    ai_usage: dict[str, Any] = field(default_factory=dict)
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
            "growth": self.growth,
            "ai_usage": self.ai_usage,
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
            "*Clear skies. Zero friction.*",
            f"*SocialAuto daily summary* · {self.team_name}",
            f"_{when}_ · last {self.days} day(s)",
            "",
            "*At a glance*",
            f"• Posts: *{o.get('total_posts', 0)}* total · "
            f"*{o.get('published_posts', 0)}* published · "
            f"*{o.get('scheduled_posts', 0)}* scheduled · "
            f"*{o.get('draft_posts', 0)}* drafts · "
            f"*{o.get('failed_posts', 0)}* need attention",
            f"• Accounts: *{o.get('connected_accounts', 0)}* connected",
            f"• Last 24h: impressions *{self.impressions_24h}* · engagement *{self.engagement_24h}*",
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
        g = self.growth
        if g:
            lines.append("")
            lines.append(f"*Growth (last {self.days}d)*")
            for f in g.get("followers", []):
                rate = (
                    f" ({f['rate']:+.1f}%)"
                    if f.get("rate") is not None
                    else ""
                )
                lines.append(
                    f"• {f['platform']}: *{f['end']}* followers · "
                    f"*{f['delta']:+d}*{rate}"
                )
            if g.get("non_follower_reach_pct") is not None:
                lines.append(
                    "• IG non-follower reach: "
                    f"*{g['non_follower_reach_pct']:.0f}%* this month"
                )
            fa = g.get("follower_adds")
            if fa:
                lines.append(
                    f"• New followers: *{fa['organic']}* organic · "
                    f"*{fa['paid']}* paid"
                )
            if g.get("peak_hours"):
                lines.append(
                    "• Best posting window (UTC): "
                    f"*{', '.join(g['peak_hours'])}*"
                )

        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        lines.append("")
        ai = self.ai_usage
        if ai:
            lines.append("*AI usage (last 24h)*")
            lines.append(
                f"• Calls: *{ai.get('total_calls', 0)}* · "
                f"Providers: *{ai.get('providers', 0)}* · "
                f"Neurons: *{ai.get('total_neurons', 0)}* · "
                f"Est. cost: *${ai.get('total_cost', 0):.4f}*"
            )
            for prov in ai.get("by_provider", [])[:5]:
                lines.append(
                    f"  - {prov.get('provider', '?')}: {prov.get('calls', 0)} calls, "
                    f"{prov.get('neurons', 0)} neurons, ${prov.get('cost', 0):.4f}"
                )
            lines.append("")
        if errors:
            lines.append(f"*Needs attention ({len(errors)})*")
            for issue in errors[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        if warnings and not errors:
            lines.append(f"*Heads up ({len(warnings)})*")
            for issue in warnings[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        if warnings and errors:
            lines.append("")
            lines.append(f"*Heads up ({len(warnings)})*")
            for issue in warnings[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        if not errors and not warnings:
            lines.append("*All clear.*")

        lines.append("")
        lines.append("_Cloudless · Clear skies. Zero friction._")
        return "\n".join(lines)


_GROWTH_EVENT_TYPES = (
    "audience_reach_split",
    "follower_attribution",
    "audience_activity",
    "follower_insights",
)


async def _growth_stats(
    db: AsyncSession, team_id: Any, since: datetime
) -> dict[str, Any]:
    """Follower deltas + audience events over the digest window."""
    rows = (
        await db.execute(
            select(
                FollowerSnapshot.platform,
                FollowerSnapshot.followers,
                FollowerSnapshot.captured_at,
            )
            .where(
                FollowerSnapshot.team_id == team_id,
                FollowerSnapshot.captured_at >= since,
            )
            .order_by(FollowerSnapshot.captured_at)
        )
    ).all()

    per: dict[str, dict[str, int]] = {}
    for platform, followers, _ts in rows:
        e = per.setdefault(platform, {"start": int(followers), "end": int(followers)})
        e["end"] = int(followers)

    growth: dict[str, Any] = {"followers": []}
    for platform, e in per.items():
        start, end = e["start"], e["end"]
        delta = end - start
        growth["followers"].append(
            {
                "platform": platform,
                "start": start,
                "end": end,
                "delta": delta,
                "rate": round(delta / start * 100, 1) if start else None,
            }
        )
    growth["followers"].sort(key=lambda f: -abs(f["delta"]))

    ev_rows = (
        await db.execute(
            select(
                AnalyticsEvent.platform,
                AnalyticsEvent.event_type,
                AnalyticsEvent.meta_data,
                AnalyticsEvent.occurred_at,
            )
            .where(
                AnalyticsEvent.team_id == team_id,
                AnalyticsEvent.occurred_at >= since,
                AnalyticsEvent.event_type.in_(_GROWTH_EVENT_TYPES),
            )
            .order_by(AnalyticsEvent.occurred_at)
        )
    ).all()

    adds = {"paid": 0, "organic": 0}
    fb_days: dict[str, dict[str, int]] = {}
    peak_hours: list[str] = []
    non_follower_pct: float | None = None
    li_gains: dict[str, int] | None = None
    for platform, etype, meta, _ts in ev_rows:
        meta = meta or {}
        if etype == "audience_reach_split" and platform == "instagram":
            ft = meta.get("by_follow_type") or {}
            total = sum(int(v or 0) for v in ft.values())
            nf = int(ft.get("NON_FOLLOWERS") or ft.get("non_followers") or 0)
            if total:
                non_follower_pct = nf / total * 100
        elif etype == "follower_attribution":
            # Per-day dict keyed by end_time — repeated syncs overwrite,
            # never double-count.
            for day, split in (meta.get("by_day") or {}).items():
                if str(day) >= since.date().isoformat():
                    fb_days[str(day)] = split or {}
        elif etype == "follower_insights" and platform == "linkedin":
            gains = (meta.get("follower_gains") or {}) or {}
            li_gains = {
                "paid": int(gains.get("paid") or 0),
                "organic": int(gains.get("organic") or 0),
            }
        elif etype == "audience_activity" and meta.get("peak_hours"):
            peak_hours = [str(h) for h in meta["peak_hours"]]

    for split in fb_days.values():
        adds["paid"] += int(split.get("paid") or 0)
        adds["organic"] += int(split.get("organic") or 0)
    if li_gains:
        adds["paid"] += li_gains["paid"]
        adds["organic"] += li_gains["organic"]

    if non_follower_pct is not None:
        growth["non_follower_reach_pct"] = round(non_follower_pct, 1)
    if adds["paid"] or adds["organic"]:
        growth["follower_adds"] = adds
    if peak_hours:
        growth["peak_hours"] = peak_hours
    if not growth["followers"] and len(growth) == 1:
        return {}
    return growth


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

    # Engagement / impressions gained in the last 24h. Snapshots store
    # cumulative platform counters and syncs capture ~every 30min, so the
    # 24h figure is the per-post delta (max - min), not the sum of all rows.
    # greatest() clamps counter resets at 0.
    per_post_delta = (
        select(
            func.greatest(
                func.max(PostAnalyticsSnapshot.impressions)
                - func.min(PostAnalyticsSnapshot.impressions),
                0,
            ).label("d_imp"),
            func.greatest(
                func.max(PostAnalyticsSnapshot.engagement)
                - func.min(PostAnalyticsSnapshot.engagement),
                0,
            ).label("d_eng"),
        )
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since_24h,
        )
        .group_by(PostAnalyticsSnapshot.platform_post_id)
        .subquery()
    )
    snap_rows = await db.execute(
        select(
            func.coalesce(func.sum(per_post_delta.c.d_imp), 0),
            func.coalesce(func.sum(per_post_delta.c.d_eng), 0),
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
        post_short = str(post.id)[:8]
        detail_src = (post.failure_reason or (post.content_text or "")[:100]).replace("\n", " ").strip()
        issues.append(
            DigestIssue(
                severity="error",
                title=f"A post didn’t publish ({post_short})",
                detail=(f"Next: open Posts → Failed to retry. Details: {detail_src}")[:200],
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
                title="Publishing couldn’t finish a queued post",
                detail=f"Next: check the linked account, then retry the post. Details: attempts {item.attempts}/{item.max_attempts}",
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
        # Soft misses that are expected states, not actionable warnings:
        # LinkedIn stale ids, no-activity markers, and X free-tier read quota
        # (persists until billing reset — a daily warning adds no signal).
        if (
            "activityids" in note
            or note.startswith("stats_unavailable")
            or "quota_exhausted" in note
            or "needs paid tier" in note
        ):
            continue
        if any(k in note for k in ("http 5", "denied", "quota", "unauthorized", "forbidden")):
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Analytics sync had trouble",
                    detail=(f"Details: {detail}")[:200],
                )
            )
            continue
        if any(k in note for k in ("error", "http 4", "fail")):
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Analytics sync had trouble",
                    detail=(f"Details: {detail}")[:200],
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
                title="No social accounts connected yet",
                detail="Next: go to Settings → Accounts and connect LinkedIn (Company Page).",
            )
        )

    # High failed rate
    if overview["failed_posts"] and overview["total_posts"]:
        rate = overview["failed_posts"] / max(overview["total_posts"], 1)
        if rate >= 0.2:
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="More posts failed than usual",
                    detail=(
                        "Next: check Accounts status, then retry failed posts. "
                        f"Details: {overview['failed_posts']}/{overview['total_posts']} failed in this window."
                    ),
                )
            )

    # AI usage summary (24h)
    ai_usage_rows = await db.execute(
        select(
            AIUsageLog.provider,
            func.count(AIUsageLog.id).label("calls"),
            func.coalesce(func.sum(AIUsageLog.actual_neurons), 0).label("neurons"),
            func.coalesce(func.sum(AIUsageLog.estimated_cost), 0).label("cost"),
        )
        .where(
            AIUsageLog.team_id == team.id,
            AIUsageLog.created_at >= since_24h,
        )
        .group_by(AIUsageLog.provider)
        .order_by(func.count(AIUsageLog.id).desc())
    )
    by_provider = [
        {"provider": p, "calls": int(c), "neurons": int(n or 0), "cost": float(co or 0)}
        for p, c, n, co in ai_usage_rows.all()
    ]
    ai_usage = {
        "total_calls": sum(p["calls"] for p in by_provider),
        "providers": len(by_provider),
        "total_neurons": sum(p["neurons"] for p in by_provider),
        "total_cost": sum(p["cost"] for p in by_provider),
        "by_provider": by_provider,
    }

    growth = await _growth_stats(db, team.id, since) if days >= 7 else {}

    return DigestReport(
        generated_at=now,
        timezone=tz_name,
        team_name=getattr(team, "name", None) or "Cloudless",
        days=days,
        overview=overview,
        impressions_24h=impressions_24h,
        engagement_24h=engagement_24h,
        top_posts=top_posts,
        growth=growth,
        issues=issues,
        ai_usage=ai_usage,
    )


async def post_digest_to_slack(report: DigestReport) -> DigestReport:
    """Send digest markdown to Slack. Prefers webhook, then bot/access token."""
    text = report.to_slack_markdown()
    ok, err, message_ts = await post_digest_text_to_slack(text)
    if ok:
        report.posted_to_slack = True
    else:
        report.slack_error = err or "unknown error"

    # If there were any warnings/errors, also post an issues-only alert to
    # #socialauto-alerts (best-effort; never blocks digest posting).
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    if errors or warnings:
        tz = ZoneInfo(report.timezone)
        when = report.generated_at.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z")
        lines = [
            "*Clear skies. Zero friction.*",
            f"*Needs attention* · {report.team_name}",
            f"_{when}_ · last {report.days} day(s)",
            "",
        ]
        if errors:
            lines.append(f"*Needs attention ({len(errors)})*")
            for issue in errors[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        if warnings:
            lines.append(f"*Heads up ({len(warnings)})*")
            for issue in warnings[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        lines.append("")
        lines.append("_Full summary is in #socialauto_")
        issues_text = "\n".join(lines)

        # Nice-to-have threading: when the digest is posted via the token path,
        # Slack returns a message timestamp (ts). Reply in-thread in #socialauto
        # so the channel stays tidy. For webhook-only posting, threading is not
        # possible (no ts), so we rely on #socialauto-alerts only.
        settings = get_settings()
        channel_id = (settings.SLACK_CHANNEL_ID or "").strip() or "C0C1F1K3DDF"
        if message_ts:
            await post_thread_reply(channel_id=channel_id, thread_ts=message_ts, text=issues_text)

        await post_alert_to_slack(issues_text)

    if report.slack_error:
        logger.warning("Slack digest post failed: %s", report.slack_error)
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
        # Skip empty/test teams when posting (still include in API preview).
        # Require real platform connectivity — teams that never connected an
        # account (E2E/test teams) only produce "No social accounts" noise.
        active = (
            report.overview.get("connected_accounts", 0) > 0
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
