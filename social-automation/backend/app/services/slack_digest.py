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
from app.models.content import Post, PostStatus, PostTarget
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
    severity: str  # error | warning | info
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
                prev = (
                    f" (prev {f['prev_delta']:+d})"
                    if f.get("prev_delta") is not None
                    else ""
                )
                fc = f" · next ~*{f['forecast']}*" if f.get("forecast") else ""
                label = f["platform"]
                if f.get("account"):
                    label += f" @{f['account']}"
                lines.append(
                    f"• {label}: *{f['end']}* followers · "
                    f"*{f['delta']:+d}*{rate}{prev}{fc}"
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
            for p in g.get("funnel", []):
                er = f" · ER *{p['er_pct']}%*" if p.get("er_pct") is not None else ""
                lines.append(
                    f"• {p['platform']}: *{p['impressions']}* imp → "
                    f"*{p['engagement']}* eng → *{p['clicks']}* clicks{er}"
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
        if warnings:
            lines.append("")
            lines.append(f"*Heads up ({len(warnings)})*")
            for issue in warnings[:8]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• *{issue.title}*{detail}")
        infos = [i for i in self.issues if i.severity == "info"]
        if infos:
            lines.append("")
            lines.append(f"_Skipped by policy ({len(infos)}) — intentional, no action needed_")
            for issue in infos[:5]:
                detail = f" — {issue.detail}" if issue.detail else ""
                lines.append(f"• {issue.title}{detail}")
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

# A FAILED post whose targets were all skipped for an intentional owner
# policy (dedup window, media-required rule, platform that can't publish)
# enforced the rules — it is informational, not an actionable error.
# (Alert-fatigue practice: only actionable items earn "Needs attention".)
_POLICY_SKIP_MARKERS = (
    "duplicate content",
    "has no media",
    "never post without media",
    "unsupported for publishing",
    "not supported for publishing",
)


def _is_policy_skip(targets: list[PostTarget]) -> bool:
    """True when every target skipped with a known policy reason."""
    if not targets:
        return False
    for t in targets:
        if t.status != "skipped":
            return False
        msg = (t.error_message or "").lower()
        if not any(m in msg for m in _POLICY_SKIP_MARKERS):
            return False
    return True


async def _growth_stats(
    db: AsyncSession,
    team_id: Any,
    since: datetime,
    prev_since: datetime | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Follower deltas + audience events + funnel over the digest window.

    When prev_since is set (monthly reports), each account also carries the
    prior window's delta for month-over-month comparison and a naive linear
    forecast of next month's count.
    """
    rows = (
        await db.execute(
            select(
                FollowerSnapshot.platform,
                FollowerSnapshot.followers,
                FollowerSnapshot.captured_at,
                FollowerSnapshot.social_account_id,
                SocialAccount.username,
            )
            .outerjoin(
                SocialAccount,
                SocialAccount.id == FollowerSnapshot.social_account_id,
            )
            .where(
                FollowerSnapshot.team_id == team_id,
                FollowerSnapshot.captured_at >= (prev_since or since),
            )
            .order_by(FollowerSnapshot.captured_at)
        )
    ).all()

    # Group per account (platform alone conflates e.g. a LinkedIn org page
    # with a personal profile). Baseline = first non-zero reading in the
    # window — early snapshots may record 0 before the platform metric is
    # available, which would turn the current count into a fake "+N".
    # Prior-window rows feed only the MoM comparison — they must not seed
    # the current baseline, or current growth would include prior gains.
    per: dict[Any, dict[str, Any]] = {}
    for platform, followers, ts, account_id, username in rows:
        key = account_id or f"{platform}:?"
        e = per.setdefault(
            key,
            {
                "platform": platform,
                "username": username,
                "start": 0,
                "end": 0,
                "prev_start": 0,
                "prev_end": 0,
                "has_prev": False,
                "has_current": False,
            },
        )
        if ts >= since:
            e["has_current"] = True
            if e["start"] == 0 and followers:
                e["start"] = int(followers)
            e["end"] = int(followers)
        else:
            e["has_prev"] = True
            if e["prev_start"] == 0 and followers:
                e["prev_start"] = int(followers)
            e["prev_end"] = int(followers)

    growth: dict[str, Any] = {"followers": []}
    for e in per.values():
        if not e["has_current"]:
            continue
        start, end = e["start"], e["end"]
        delta = end - start
        row: dict[str, Any] = {
            "platform": e["platform"],
            "account": e["username"],
            "start": start,
            "end": end,
            "delta": delta,
            "rate": round(delta / start * 100, 1) if start else None,
        }
        if e["has_prev"]:
            row["prev_delta"] = e["prev_end"] - e["prev_start"]
            row["forecast"] = end + round(delta * 30 / days)
        growth["followers"].append(row)
    growth["followers"].sort(key=lambda f: -abs(f["delta"]))

    # Post-level funnel for the window: impressions → engagement → clicks,
    # summed per-post deltas (greatest clamps counter resets at 0).
    funnel_delta = (
        select(
            PostAnalyticsSnapshot.platform.label("platform"),
            func.greatest(
                func.max(PostAnalyticsSnapshot.impressions)
                - func.min(PostAnalyticsSnapshot.impressions),
                0,
            ).label("imp"),
            func.greatest(
                func.max(PostAnalyticsSnapshot.engagement)
                - func.min(PostAnalyticsSnapshot.engagement),
                0,
            ).label("eng"),
            func.greatest(
                func.max(PostAnalyticsSnapshot.clicks)
                - func.min(PostAnalyticsSnapshot.clicks),
                0,
            ).label("clk"),
        )
        .where(
            PostAnalyticsSnapshot.team_id == team_id,
            PostAnalyticsSnapshot.captured_at >= since,
            PostAnalyticsSnapshot.platform_post_id.isnot(None),
        )
        .group_by(
            PostAnalyticsSnapshot.platform_post_id,
            PostAnalyticsSnapshot.platform,
        )
        .subquery()
    )
    funnel_rows = (
        await db.execute(
            select(
                funnel_delta.c.platform,
                func.sum(funnel_delta.c.imp),
                func.sum(funnel_delta.c.eng),
                func.sum(funnel_delta.c.clk),
            ).group_by(funnel_delta.c.platform)
        )
    ).all()
    funnel = [
        {
            "platform": p,
            "impressions": int(i or 0),
            "engagement": int(e or 0),
            "clicks": int(c or 0),
            "er_pct": round(int(e or 0) / int(i) * 100, 1) if i else None,
        }
        for p, i, e, c in funnel_rows
    ]
    funnel.sort(key=lambda f: -f["impressions"])
    if funnel:
        growth["funnel"] = funnel

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
            nf = next(
                (int(ft[k]) for k in ft if "non_follower" in str(k).lower()),
                0,
            )
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
            # Campaign-level ad rows aren't posts — ads have their own digest.
            PostAnalyticsSnapshot.source != "linkedin_ads",
        )
        .subquery()
    )
    top_q = await db.execute(
        select(
            PostAnalyticsSnapshot.post_id,
            PostAnalyticsSnapshot.platform_post_id,
            PostAnalyticsSnapshot.platform,
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
    for post_id, platform_post_id, platform, imps, eng, text in top_q.all():
        snippet = (text or "").strip()
        if not snippet and platform_post_id:
            snippet = f"{platform} post {platform_post_id[-8:]}"
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
    failed_list = failed_posts.scalars().all()
    # Batched target lookup: an all-skipped post with policy reasons only
    # (dedup, media-required, unsupported platform) is informational.
    policy_skipped = 0
    targets_by_post: dict[Any, list[PostTarget]] = {}
    if failed_list:
        tgt_rows = await db.execute(
            select(PostTarget).where(
                PostTarget.post_id.in_([p.id for p in failed_list])
            )
        )
        for t in tgt_rows.scalars().all():
            targets_by_post.setdefault(t.post_id, []).append(t)
    for post in failed_list:
        post_short = str(post.id)[:8]
        detail_src = (post.failure_reason or (post.content_text or "")[:100]).replace("\n", " ").strip()
        ptargets = targets_by_post.get(post.id, [])
        if _is_policy_skip(ptargets):
            policy_skipped += 1
            reason = (ptargets[0].error_message or "").strip()
            issues.append(
                DigestIssue(
                    severity="info",
                    title=f"A post was skipped by policy ({post_short})",
                    detail=reason[:180],
                )
            )
            continue
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

    # Snapshot notes that look like errors (24h). Notes on disconnected
    # accounts are skipped — the account status already tells the user to
    # reconnect; repeating its sync errors daily adds no signal.
    bad_snaps = await db.execute(
        select(PostAnalyticsSnapshot)
        .join(
            SocialAccount,
            SocialAccount.id == PostAnalyticsSnapshot.social_account_id,
        )
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since_24h,
            PostAnalyticsSnapshot.notes.isnot(None),
            SocialAccount.status != "disconnected",
        )
        .order_by(PostAnalyticsSnapshot.captured_at.desc())
        .limit(30)
    )
    # Latest snapshot note per account — a stale error note that has since
    # recovered (a newer snapshot exists with a clean or benign note) is
    # noise; warning on it for the rest of the 24h window adds no signal.
    latest_note_rows = await db.execute(
        select(
            PostAnalyticsSnapshot.social_account_id,
            PostAnalyticsSnapshot.notes,
        )
        .distinct(PostAnalyticsSnapshot.social_account_id)
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since_24h,
        )
        .order_by(
            PostAnalyticsSnapshot.social_account_id,
            PostAnalyticsSnapshot.captured_at.desc(),
        )
    )
    latest_note = {acct: (n or "").lower() for acct, n in latest_note_rows.all()}
    _err_keys = ("http 5", "denied", "quota", "unauthorized", "forbidden",
                 "error", "http 4", "fail")
    _info_markers = ("activityids", "stats_unavailable", "quota_exhausted",
                     "needs paid tier", "organization_lifetime",
                     "member_stats_not_implemented")
    for snap in bad_snaps.scalars().all():
        note = (snap.notes or "").lower()
        detail = (snap.notes or "")[:200]
        latest = latest_note.get(snap.social_account_id, "")
        # Recovered transient — skip if the account's newest snapshot is
        # clean (or only carries an informational marker).
        if any(m in latest for m in _info_markers) or not any(
            k in latest for k in _err_keys
        ):
            continue
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
        # Meta checkpoint (OAuthException 190/459) — needs a human to log
        # into facebook.com and complete the prompt, then reconnect.
        if "cannot access the app" in note:
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="Reconnect a social account",
                    detail=(
                        "Facebook requires a login checkpoint — open "
                        "facebook.com, complete the prompt, then reconnect "
                        "the account in SocialAuto → Accounts."
                    ),
                )
            )
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

    # High failed rate — policy-skipped posts don't count toward this:
    # intentional guardrail enforcement is not a failure signal.
    effective_failed = max(0, (overview["failed_posts"] or 0) - policy_skipped)
    if effective_failed and overview["total_posts"]:
        rate = effective_failed / max(overview["total_posts"], 1)
        if rate >= 0.2:
            issues.append(
                DigestIssue(
                    severity="warning",
                    title="More posts failed than usual",
                    detail=(
                        "Next: check Accounts status, then retry failed posts. "
                        f"Details: {effective_failed}/{overview['total_posts']} failed in this window."
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

    growth: dict[str, Any] = {}
    if days >= 7:
        prev_since = since - timedelta(days=days) if days >= 28 else None
        growth = await _growth_stats(
            db, team.id, since, prev_since=prev_since, days=days
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
