"""Insights engine — combines per-platform analytics into recommendations.

Reads PostAnalyticsSnapshot (post metrics), FollowerSnapshot (growth),
AnalyticsEvent account_insights/audience_demographics (account-level) and
produces:

- per-platform performance stats + momentum (week-over-week)
- best posting windows (hour + weekday, Europe/Athens)
- content-type performance (media vs text)
- audience demographics (where available — IG)
- actionable recommendations grounded in each platform's documented
  best practices (PLATFORM_BEST_PRACTICES)

Pure computation over stored data — no live API calls, no LLM, so it is
deterministic and unit-testable.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
from app.models.content import Post, PostTarget

ATHENS = ZoneInfo("Europe/Athens")

# Documented best practices per platform (public platform guidance +
# widely-cited engagement studies). `focus` mirrors the Visibility Era
# 2-platform rule: main / secondary / last.
PLATFORM_BEST_PRACTICES: dict[str, dict[str, Any]] = {
    "linkedin": {
        "focus": "main",
        "cadence": "1 post/day max; 3–5/week is healthy",
        "best_windows": "Tue–Thu, 08:00–11:00 local (business audience)",
        "formats": "Document/carousel posts and native text outperform links; "
                   "carousels get ~2x dwell time",
        "ads": "LinkedIn ads are expensive (CPL €30+) — boost only proven "
               "organic winners; organic + employee amplification first",
        "source": "LinkedIn Marketing Solutions docs",
    },
    "instagram": {
        "focus": "secondary",
        "cadence": "3–5 feed posts/week + Reels for reach",
        "best_windows": "11:00–13:00 and 19:00–21:00 local",
        "formats": "Reels for reach, carousels for engagement; every post "
                   "needs media — no text-only",
        "ads": "Boost top-engagement posts via Meta Ads Manager; target "
               "follower lookalikes in top countries (see demographics)",
        "source": "Instagram Creators + Meta Business docs",
    },
    "facebook": {
        "focus": "secondary",
        "cadence": "1–2 posts/day on the Page",
        "best_windows": "09:00–13:00 weekdays local",
        "formats": "Native video and image posts; avoid engagement-bait phrasing",
        "ads": "Meta Ads Manager on the Page; use page_post_engagements + "
               "follower trends to pick boost candidates",
        "source": "Meta Business Help Center",
    },
    "threads": {
        "focus": "secondary",
        "cadence": "2–3 posts/day is fine — conversational platform",
        "best_windows": "Weekday mornings + lunch",
        "formats": "Short text + replies; conversation drives distribution",
        "ads": "No Threads ads API yet — organic only",
        "source": "Threads API docs + creator guidance",
    },
    "twitter": {
        "focus": "last",
        "cadence": "1–3/day (free tier ~1.5k posts/mo quota — budget it)",
        "best_windows": "Weekdays 09:00–15:00 local",
        "formats": "Short text + threads; links de-prioritized",
        "ads": "Skip — free-tier quota is the binding constraint",
        "source": "X API docs",
    },
    "tiktok": {
        "focus": "last",
        "cadence": "1–4/day for growth; consistency > volume",
        "best_windows": "18:00–22:00 local",
        "formats": "Short video only; watch time + completion rate drive "
                   "the algorithm",
        "ads": "TikTok Ads only if video content already performs organically",
        "source": "TikTok Creator Center",
    },
}


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round(((current - previous) / previous) * 100, 1)


async def build_team_insights(
    db: AsyncSession, team_id: uuid.UUID, *, days: int = 90
) -> dict[str, Any]:
    """Compute cross-platform insights + recommendations for a team."""
    since = datetime.now(UTC) - timedelta(days=days)
    week_ago = datetime.now(UTC) - timedelta(days=7)
    two_weeks_ago = datetime.now(UTC) - timedelta(days=14)

    # ── Latest snapshot per post, joined to the post for publish time/type ──
    ranked = (
        select(
            PostAnalyticsSnapshot.id,
            func.row_number()
            .over(
                partition_by=(
                    PostAnalyticsSnapshot.social_account_id,
                    PostAnalyticsSnapshot.platform_post_id,
                ),
                order_by=PostAnalyticsSnapshot.captured_at.desc(),
            )
            .label("rn"),
        )
        .where(
            PostAnalyticsSnapshot.team_id == team_id,
            PostAnalyticsSnapshot.captured_at >= since,
            PostAnalyticsSnapshot.platform_post_id.isnot(None),
            PostAnalyticsSnapshot.source != "linkedin_org_lifetime",
        )
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                PostAnalyticsSnapshot.platform,
                PostAnalyticsSnapshot.post_id,
                PostAnalyticsSnapshot.impressions,
                PostAnalyticsSnapshot.engagement,
                PostAnalyticsSnapshot.engagement_rate,
                PostAnalyticsSnapshot.likes,
                PostAnalyticsSnapshot.comments,
                PostAnalyticsSnapshot.shares,
                Post.published_at,
                Post.created_at,
                Post.media_ids,
            )
            .join(Post, Post.id == PostAnalyticsSnapshot.post_id)
            .where(PostAnalyticsSnapshot.id.in_(select(ranked.c.id).where(ranked.c.rn == 1)))
        )
    ).all()

    # ── Aggregate per platform ─────────────────────────────────────────────
    plat: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "posts": 0,
            "impressions": 0,
            "engagement": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "hours": defaultdict(lambda: [0, 0.0]),   # hour -> [n, er_sum]
            "weekdays": defaultdict(lambda: [0, 0.0]),  # weekday -> [n, er_sum]
            "media_posts": [0, 0.0],
            "text_posts": [0, 0.0],
            "recent_engagement": 0,
            "prior_engagement": 0,
            "recent_impressions": 0,
            "prior_impressions": 0,
            "top_post": None,
        }
    )
    for r in rows:
        p = plat[r.platform]
        er = float(r.engagement_rate or 0)
        p["posts"] += 1
        p["impressions"] += int(r.impressions or 0)
        p["engagement"] += int(r.engagement or 0)
        p["likes"] += int(r.likes or 0)
        p["comments"] += int(r.comments or 0)
        p["shares"] += int(r.shares or 0)

        pub = r.published_at or r.created_at
        if pub:
            local = pub.astimezone(ATHENS) if pub.tzinfo else pub.replace(tzinfo=UTC).astimezone(ATHENS)
            p["hours"][local.hour][0] += 1
            p["hours"][local.hour][1] += er
            p["weekdays"][local.weekday()][0] += 1
            p["weekdays"][local.weekday()][1] += er
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=UTC)
            if pub >= week_ago:
                p["recent_engagement"] += int(r.engagement or 0)
                p["recent_impressions"] += int(r.impressions or 0)
            elif pub >= two_weeks_ago:
                p["prior_engagement"] += int(r.engagement or 0)
                p["prior_impressions"] += int(r.impressions or 0)

        bucket = "media_posts" if r.media_ids else "text_posts"
        p[bucket][0] += 1
        p[bucket][1] += er

        if p["top_post"] is None or er > p["top_post"]["engagement_rate"]:
            p["top_post"] = {"post_id": str(r.post_id), "engagement_rate": er,
                             "impressions": int(r.impressions or 0)}

    # ── Follower trends ────────────────────────────────────────────────────
    follower_rows = (
        await db.execute(
            select(
                FollowerSnapshot.platform,
                FollowerSnapshot.social_account_id,
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
    follower_windows: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for fr in follower_rows:
        follower_windows[f"{fr.platform}:{fr.social_account_id}"].append(
            (fr.captured_at, int(fr.followers))
        )
    follower_growth: dict[str, dict[str, Any]] = {}
    for key, series in follower_windows.items():
        platform = key.split(":", 1)[0]
        first, last = series[0][1], series[-1][1]
        g = follower_growth.setdefault(
            platform, {"first": 0, "last": 0, "accounts": 0}
        )
        g["first"] += first
        g["last"] += last
        g["accounts"] += 1

    # ── Latest account-insight events per platform ─────────────────────────
    ev_rows = (
        await db.execute(
            select(AnalyticsEvent.platform, AnalyticsEvent.event_type, AnalyticsEvent.meta_data)
            .where(
                AnalyticsEvent.team_id == team_id,
                AnalyticsEvent.event_type.in_(
                    ["account_insights", "audience_demographics"]
                ),
                AnalyticsEvent.occurred_at >= since,
            )
            .order_by(AnalyticsEvent.occurred_at.desc())
        )
    ).all()
    latest_events: dict[tuple[str, str], dict] = {}
    for platform, event_type, meta in ev_rows:
        latest_events.setdefault((platform, event_type), meta or {})

    # ── Compose per-platform report ────────────────────────────────────────
    platforms: dict[str, dict[str, Any]] = {}
    for name, p in plat.items():
        posts = p["posts"]
        avg_er = round(p["engagement"] / p["impressions"] * 100, 2) if p["impressions"] else 0.0

        def _best(buckets: dict[int, list[int]], min_n: int = 2):
            cands = [
                (k, round(v[1] / v[0], 2)) for k, v in buckets.items() if v[0] >= min_n
            ]
            return max(cands, key=lambda kv: kv[1]) if cands else None

        best_hour = _best(p["hours"])
        best_weekday = _best(p["weekdays"])
        media_er = (
            round(p["media_posts"][1] / p["media_posts"][0], 2) if p["media_posts"][0] else None
        )
        text_er = (
            round(p["text_posts"][1] / p["text_posts"][0], 2) if p["text_posts"][0] else None
        )
        momentum = _pct_change(p["recent_engagement"], p["prior_engagement"])

        growth = follower_growth.get(name)
        platforms[name] = {
            "focus_tier": PLATFORM_BEST_PRACTICES.get(name, {}).get("focus", "last"),
            "posts": posts,
            "impressions": p["impressions"],
            "engagement": p["engagement"],
            "likes": p["likes"],
            "comments": p["comments"],
            "shares": p["shares"],
            "avg_engagement_rate": avg_er,
            "best_hour_athens": best_hour,
            "best_weekday_athens": best_weekday,
            "media_avg_er": media_er,
            "text_avg_er": text_er,
            "momentum_7d_engagement_pct": momentum,
            "momentum_7d_impressions_pct": _pct_change(
                p["recent_impressions"], p["prior_impressions"]
            ),
            "follower_growth": (
                {
                    "net": growth["last"] - growth["first"],
                    "current": growth["last"],
                    "accounts": growth["accounts"],
                }
                if growth
                else None
            ),
            "account_insights": latest_events.get((name, "account_insights")),
            "audience_demographics": latest_events.get((name, "audience_demographics")),
        }

    # ── Recommendations ────────────────────────────────────────────────────
    recs = _recommend(platforms)
    return {
        "window_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "platforms": platforms,
        "recommendations": recs,
        "best_practices": PLATFORM_BEST_PRACTICES,
    }


_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _recommend(platforms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule-based recommendations grounded in platform best practices."""
    recs: list[dict[str, Any]] = []
    if not platforms:
        return [{
            "type": "setup",
            "priority": "high",
            "text": "No post metrics yet — publish a few posts per platform, "
                    "then re-check insights.",
        }]

    ranked = sorted(
        platforms.items(),
        key=lambda kv: kv[1]["avg_engagement_rate"],
        reverse=True,
    )

    # 1. Channel focus — where engagement is actually landing.
    top_name, top = ranked[0]
    if top["posts"] >= 3:
        recs.append({
            "type": "channel_focus",
            "priority": "high",
            "platform": top_name,
            "text": (
                f"{top_name} is your strongest channel "
                f"({top['avg_engagement_rate']}% avg engagement over "
                f"{top['posts']} posts). Keep original content here first; "
                "adapt it to secondary platforms after."
            ),
        })

    weak = []
    for name, p in ranked[1:]:
        if (
            p["posts"] >= 3
            and p["engagement"] > 0
            and top["avg_engagement_rate"] > 0
            and p["avg_engagement_rate"] / top["avg_engagement_rate"] < 0.4
        ):
            weak.append(name)
    if weak:
        recs.append({
            "type": "deprioritize",
            "priority": "medium",
            "platform": ", ".join(weak),
            "text": (
                f"{', '.join(weak)} engagement is <40% of {top_name}'s — "
                "cross-post adapted versions only, don't craft "
                "platform-first content there this sprint."
            ),
        })

    # 2. Best posting windows — only when the winning bucket actually
    # produced engagement (ER > 0); otherwise use the platform baseline.
    for name, p in platforms.items():
        bp = PLATFORM_BEST_PRACTICES.get(name)
        has_signal = (
            p["best_weekday_athens"] and p["best_hour_athens"]
            and p["best_weekday_athens"][1] > 0
            and p["best_hour_athens"][1] > 0
        )
        if has_signal:
            wd, wd_er = p["best_weekday_athens"]
            hr, hr_er = p["best_hour_athens"]
            recs.append({
                "type": "timing",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"Your {name} audience engages most on {_WEEKDAYS[wd]} "
                    f"around {hr:02d}:00 Athens time ({wd_er}% / {hr_er}% avg "
                    f"ER in those buckets). Schedule there; platform baseline: "
                    f"{bp['best_windows'] if bp else 'n/a'}."
                ),
            })
        elif bp:
            recs.append({
                "type": "timing",
                "priority": "low",
                "platform": name,
                "text": (
                    f"Not enough {name} post data for a custom window yet — "
                    f"use the platform baseline: {bp['best_windows']}."
                ),
            })

    # 3. Format recommendations (media vs text) — needs real signal.
    for name, p in platforms.items():
        if (
            p["media_avg_er"] is not None and p["text_avg_er"] is not None
            and (p["media_avg_er"] > 0 or p["text_avg_er"] > 0)
        ):
            if p["media_avg_er"] > p["text_avg_er"] * 1.2:
                recs.append({
                    "type": "format",
                    "priority": "medium",
                    "platform": name,
                    "text": (
                        f"Media posts beat text on {name} "
                        f"({p['media_avg_er']}% vs {p['text_avg_er']}% ER) — "
                        "attach an image/carousel to every post."
                    ),
                })
            elif p["text_avg_er"] > p["media_avg_er"] * 1.2:
                recs.append({
                    "type": "format",
                    "priority": "medium",
                    "platform": name,
                    "text": (
                        f"Text posts outperform media on {name} "
                        f"({p['text_avg_er']}% vs {p['media_avg_er']}% ER) — "
                        "lead with strong copy, media optional."
                    ),
                })

    # 4. Momentum — rising/declining channels.
    for name, p in platforms.items():
        mom = p.get("momentum_7d_engagement_pct")
        if mom is not None and mom >= 25:
            recs.append({
                "type": "momentum",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} engagement is up {mom}% week-over-week — "
                    "increase cadence now while the algorithm favors you."
                ),
            })
        elif mom is not None and mom <= -25:
            recs.append({
                "type": "momentum",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} engagement dropped {abs(mom)}% week-over-week — "
                    "review the last 7 days' content mix before scheduling more."
                ),
            })

    # 5. Follower growth signals.
    for name, p in platforms.items():
        g = p.get("follower_growth")
        if g and g["net"] < 0 and p["posts"] > 0:
            recs.append({
                "type": "growth",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} lost {abs(g['net'])} followers in the window — "
                    "check content cadence/relevance; consider a re-engagement post."
                ),
            })

    # 6. Ads guidance — boost proven organic winners.
    for name, p in platforms.items():
        bp = PLATFORM_BEST_PRACTICES.get(name)
        top_post = p.get("top_post")
        if (
            bp and "no " not in bp["ads"][:5].lower()
            and top_post and p["posts"] >= 5 and top_post["engagement_rate"] > 0
        ):
            recs.append({
                "type": "ads",
                "priority": "low",
                "platform": name,
                "text": (
                    f"{name}: {bp['ads']} Top organic post hit "
                    f"{top_post['engagement_rate']}% ER — it's the boost "
                    "candidate if you run paid."
                ),
            })

    # 7. Cross-posting suggestion — main → secondary.
    main = next(
        (p for p in platforms.values() if p["focus_tier"] == "main"), None
    )
    if main and main["posts"] > 0:
        secondary = [
            n for n, p in platforms.items() if p["focus_tier"] == "secondary"
        ]
        if secondary:
            recs.append({
                "type": "crosspost",
                "priority": "medium",
                "text": (
                    f"Repurpose your top LinkedIn post to "
                    f"{', '.join(secondary)} — adapt format per platform "
                    "(carousel→IG carousel, short text→Threads). "
                    "Main-platform content leads; Meta gets adaptations."
                ),
            })

    return recs
