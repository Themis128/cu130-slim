"""Insights engine — combines per-platform analytics into recommendations.

Reads PostAnalyticsSnapshot (post metrics), FollowerSnapshot (growth),
AnalyticsEvent account_insights/audience_demographics (account-level) and
produces:

- per-platform performance stats + momentum (week-over-week, measured as
  engagement *received* in each 7-day window via snapshot deltas)
- best posting windows (hour + weekday, Europe/Athens)
- content-type performance (media vs text)
- audience demographics (where available — IG)
- confidence + benchmark calibration against published industry medians
- actionable recommendations grounded in each platform's documented
  best practices (PLATFORM_BEST_PRACTICES)

Structure: ``build_team_insights`` is the async DB boundary; all scoring lives
in ``compute_insights``/``_recommend`` which are pure functions over plain
rows — deterministic and unit-testable without a database.
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
from app.models.content import Post

ATHENS = ZoneInfo("Europe/Athens")

# Documented best practices per platform (public platform guidance +
# widely-cited engagement studies). `focus` mirrors the Visibility Era
# 2-platform rule: main / secondary / last.
PLATFORM_BEST_PRACTICES: dict[str, dict[str, Any]] = {
    "linkedin": {
        "focus": "main",
        "ads_available": True,
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
        "ads_available": True,
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
        "ads_available": True,
        "cadence": "1–2 posts/day on the Page",
        "best_windows": "09:00–13:00 weekdays local",
        "formats": "Native video and image posts; avoid engagement-bait phrasing",
        "ads": "Meta Ads Manager on the Page; use page_post_engagements + "
               "follower trends to pick boost candidates",
        "source": "Meta Business Help Center",
    },
    "threads": {
        "focus": "secondary",
        "ads_available": False,
        "cadence": "2–3 posts/day is fine — conversational platform",
        "best_windows": "Weekday mornings + lunch",
        "formats": "Short text + replies; conversation drives distribution",
        "ads": "No Threads ads API yet — organic only",
        "source": "Threads API docs + creator guidance",
    },
    "twitter": {
        "focus": "last",
        "ads_available": False,
        "cadence": "1–3/day (free tier ~1.5k posts/mo quota — budget it)",
        "best_windows": "Weekdays 09:00–15:00 local",
        "formats": "Short text + threads; links de-prioritized",
        "ads": "Skip — free-tier quota is the binding constraint",
        "source": "X API docs",
    },
    "tiktok": {
        "focus": "last",
        "ads_available": True,
        "cadence": "1–4/day for growth; consistency > volume",
        "best_windows": "18:00–22:00 local",
        "formats": "Short video only; watch time + completion rate drive "
                   "the algorithm",
        "ads": "TikTok Ads only if video content already performs organically",
        "source": "TikTok Creator Center",
    },
}

# Published engagement-rate benchmarks, ER BY FOLLOWERS (interactions ÷
# followers) — the methodology every cited source uses. Our snapshots measure
# ER by impressions, so the engine also computes er_by_followers to compare
# like-for-like. Industry column = Tech & Software where published.
PLATFORM_BENCHMARKS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "er_by_followers_pct": 5.20,
        "method": "avg engagements/post ÷ followers",
        "industry_pct": 3.60,  # Hootsuite Technology, avg engagement/post
        "per_format_pct": {
            "native_document": 7.00, "multi_image": 6.45, "video": 6.00,
            "image": 5.30, "text": 4.50, "poll": 4.20, "link": 3.25,
        },
        "source": "Socialinsider 2025 (1.3M business posts) + Hootsuite",
    },
    "instagram": {
        "er_by_followers_pct": 0.36,
        "method": "median interactions/post ÷ followers",
        "industry_pct": 0.33,  # Rival IQ Tech & Software
        "source": "Rival IQ 2025 Benchmark Report",
    },
    "facebook": {
        "er_by_followers_pct": 0.063,
        "method": "median interactions/post ÷ followers",
        "industry_pct": 0.02,  # Rival IQ Tech & Software
        "source": "Rival IQ 2025 Benchmark Report",
    },
    "threads": {
        "er_by_followers_pct": 4.51,
        "method": "avg engagement rate",
        "industry_pct": None,
        "source": "Buffer 2025 cross-platform analysis",
    },
    "twitter": {
        "er_by_followers_pct": 0.029,
        "method": "median interactions/post ÷ followers",
        "industry_pct": 0.02,  # Rival IQ Tech & Software
        "source": "Rival IQ 2025 Benchmark Report",
    },
    "tiktok": {
        "er_by_followers_pct": 1.73,
        "method": "median interactions/post ÷ followers",
        "industry_pct": 1.21,  # Rival IQ Tech & Software
        "source": "Rival IQ 2025 Benchmark Report",
    },
}

# Snapshot notes that mean "data collection is broken, not the content".
_DATA_GAP_NOTES = (
    "insights_scope_missing",
    "stats_unavailable",
    "session expired",
    "token",
    "HTTP 401",
    "HTTP 403",
)

# Minimum evidence before momentum recommendations fire (suppresses
# small-number noise like 1→2 engagements = +100%).
_MOMENTUM_MIN_EVENTS = 5


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _confidence(posts: int, impressions: int) -> str:
    """Statistical confidence in this platform's numbers."""
    if posts >= 10 and impressions >= 500:
        return "high"
    if posts >= 5 and impressions >= 100:
        return "medium"
    return "low"


def _window_delta(
    points: list[tuple[datetime, int, int]], start: datetime, end: datetime
) -> tuple[int, int]:
    """Engagement/impressions *received* inside [start, end).

    Snapshots are cumulative lifetime counters, so the delta is last-in-window
    minus the latest value at or before window start (captures boundary
    crossing). Returns (engagement_delta, impressions_delta).
    """
    before = [p for p in points if p[0] < start]
    inside = [p for p in points if start <= p[0] < end]
    if not inside:
        return 0, 0
    base = before[-1] if before else inside[0]
    last = inside[-1]
    return max(0, last[1] - base[1]), max(0, last[2] - base[2])


async def build_team_insights(
    db: AsyncSession, team_id: uuid.UUID, *, days: int = 90
) -> dict[str, Any]:
    """DB boundary: fetch rows, delegate scoring to compute_insights."""
    since = datetime.now(UTC) - timedelta(days=days)
    two_weeks_ago = datetime.now(UTC) - timedelta(days=14)

    # Latest snapshot per post, joined to the post for publish time/type.
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
    post_rows = (
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
                PostAnalyticsSnapshot.notes,
                Post.published_at,
                Post.created_at,
                Post.media_ids,
            )
            .join(Post, Post.id == PostAnalyticsSnapshot.post_id)
            .where(PostAnalyticsSnapshot.id.in_(select(ranked.c.id).where(ranked.c.rn == 1)))
        )
    ).all()

    # All snapshots in the last 14 days — momentum is engagement *received*
    # per week, computed from cumulative-counter deltas, not publish dates.
    delta_rows = (
        await db.execute(
            select(
                PostAnalyticsSnapshot.platform,
                PostAnalyticsSnapshot.platform_post_id,
                PostAnalyticsSnapshot.captured_at,
                PostAnalyticsSnapshot.engagement,
                PostAnalyticsSnapshot.impressions,
            )
            .where(
                PostAnalyticsSnapshot.team_id == team_id,
                PostAnalyticsSnapshot.captured_at >= two_weeks_ago,
                PostAnalyticsSnapshot.platform_post_id.isnot(None),
                PostAnalyticsSnapshot.source != "linkedin_org_lifetime",
            )
            .order_by(PostAnalyticsSnapshot.captured_at)
        )
    ).all()

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

    return compute_insights(
        post_rows, delta_rows, follower_rows, ev_rows, days=days
    )


def compute_insights(
    post_rows: list[Any],
    delta_rows: list[Any],
    follower_rows: list[Any],
    ev_rows: list[Any],
    *,
    days: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure scoring: rows in → platforms report + recommendations out."""
    now = now or datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

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
            "notes": set(),
            "top_post": None,
        }
    )
    for r in post_rows:
        p = plat[r.platform]
        er = float(r.engagement_rate or 0)
        p["posts"] += 1
        p["impressions"] += int(r.impressions or 0)
        p["engagement"] += int(r.engagement or 0)
        p["likes"] += int(r.likes or 0)
        p["comments"] += int(r.comments or 0)
        p["shares"] += int(r.shares or 0)
        if r.notes:
            p["notes"].add(r.notes)

        pub = r.published_at or r.created_at
        if pub:
            local = pub.astimezone(ATHENS) if pub.tzinfo else pub.replace(tzinfo=UTC).astimezone(ATHENS)
            p["hours"][local.hour][0] += 1
            p["hours"][local.hour][1] += er
            p["weekdays"][local.weekday()][0] += 1
            p["weekdays"][local.weekday()][1] += er

        bucket = "media_posts" if r.media_ids else "text_posts"
        p[bucket][0] += 1
        p[bucket][1] += er

        if p["top_post"] is None or er > p["top_post"]["engagement_rate"]:
            p["top_post"] = {"post_id": str(r.post_id), "engagement_rate": er,
                             "impressions": int(r.impressions or 0)}

    # ── Momentum: cumulative-snapshot deltas per 7-day window ──────────────
    snap_series: dict[str, list[tuple[datetime, int, int]]] = defaultdict(list)
    for r in delta_rows:
        key = f"{r.platform}:{r.platform_post_id}"
        cap = r.captured_at if r.captured_at.tzinfo else r.captured_at.replace(tzinfo=UTC)
        snap_series[key].append((cap, int(r.engagement or 0), int(r.impressions or 0)))
    for pts in snap_series.values():
        pts.sort(key=lambda p: p[0])

    momentum: dict[str, dict[str, int]] = defaultdict(
        lambda: {"recent_eng": 0, "prior_eng": 0, "recent_imp": 0, "prior_imp": 0}
    )
    for key, pts in snap_series.items():
        platform = key.split(":", 1)[0]
        re_, ri = _window_delta(pts, week_ago, now)
        pe, pi = _window_delta(pts, two_weeks_ago, week_ago)
        m = momentum[platform]
        m["recent_eng"] += re_
        m["recent_imp"] += ri
        m["prior_eng"] += pe
        m["prior_imp"] += pi

    # ── Follower trends ────────────────────────────────────────────────────
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

        media_er = (
            round(p["media_posts"][1] / p["media_posts"][0], 2) if p["media_posts"][0] else None
        )
        text_er = (
            round(p["text_posts"][1] / p["text_posts"][0], 2) if p["text_posts"][0] else None
        )
        mom = momentum.get(name, {})

        growth = follower_growth.get(name)
        followers_now = growth["last"] if growth else None

        # ER by followers — the metric every published benchmark uses.
        # avg engagements per post ÷ current followers.
        er_by_followers = (
            round(p["engagement"] / posts / followers_now * 100, 3)
            if posts and followers_now
            else None
        )
        bench = PLATFORM_BENCHMARKS.get(name)
        bench_ref = bench["industry_pct"] or bench["er_by_followers_pct"] if bench else None
        benchmark = None
        if bench and er_by_followers is not None and bench_ref:
            ratio = round(er_by_followers / bench_ref, 2)
            benchmark = {
                "your_er_by_followers_pct": er_by_followers,
                "benchmark_pct": bench_ref,
                "ratio": ratio,
                "verdict": (
                    "above_benchmark" if ratio >= 1.2
                    else "at_benchmark" if ratio >= 0.7
                    else "below_benchmark"
                ),
                "method": bench["method"],
                "source": bench["source"],
            }

        data_warnings = sorted(
            n for n in p["notes"]
            if n and any(sig in n for sig in _DATA_GAP_NOTES)
        )

        platforms[name] = {
            "focus_tier": PLATFORM_BEST_PRACTICES.get(name, {}).get("focus", "last"),
            "confidence": _confidence(posts, p["impressions"]),
            "posts": posts,
            "impressions": p["impressions"],
            "engagement": p["engagement"],
            "likes": p["likes"],
            "comments": p["comments"],
            "shares": p["shares"],
            "avg_engagement_rate": avg_er,
            "er_by_followers_pct": er_by_followers,
            "benchmark": benchmark,
            "best_hour_athens": _best(p["hours"]),
            "best_weekday_athens": _best(p["weekdays"]),
            "media_avg_er": media_er,
            "text_avg_er": text_er,
            "momentum_7d_engagement_pct": _pct_change(
                mom.get("recent_eng", 0), mom.get("prior_eng", 0)
            ),
            "momentum_7d_impressions_pct": _pct_change(
                mom.get("recent_imp", 0), mom.get("prior_imp", 0)
            ),
            "engagement_7d": mom.get("recent_eng", 0),
            "engagement_prev_7d": mom.get("prior_eng", 0),
            "follower_growth": (
                {
                    "net": growth["last"] - growth["first"],
                    "current": growth["last"],
                    "accounts": growth["accounts"],
                }
                if growth
                else None
            ),
            "data_warnings": data_warnings,
            "account_insights": latest_events.get((name, "account_insights")),
            "audience_demographics": latest_events.get((name, "audience_demographics")),
        }

    recs = _recommend(platforms)
    return {
        "window_days": days,
        "generated_at": now.isoformat(),
        "platforms": platforms,
        "recommendations": recs,
        "best_practices": PLATFORM_BEST_PRACTICES,
        "benchmarks": PLATFORM_BENCHMARKS,
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

    # Rank by benchmark-relative performance when available (comparable
    # across platforms), else raw ER-by-impressions.
    def _score(p: dict[str, Any]) -> float:
        b = p.get("benchmark")
        if b:
            return b["ratio"]
        return p["avg_engagement_rate"]

    ranked = sorted(platforms.items(), key=lambda kv: _score(kv[1]), reverse=True)

    # 0. Data gaps first — fix collection before trusting any other signal.
    for name, p in platforms.items():
        for w in p.get("data_warnings", []):
            if "insights_scope_missing" in w:
                text = (
                    f"{name} insights are permission-blocked — reconnect the "
                    f"account so it grants the insights scope ({w})."
                )
            else:
                text = f"{name} data collection issue: {w}"
            recs.append({
                "type": "data_gap", "priority": "high",
                "platform": name, "text": text,
            })
            break  # one data-gap rec per platform

    # 1. Channel focus — where engagement is actually landing.
    top_name, top = ranked[0]
    if top["posts"] >= 3:
        conf_note = (
            "" if top["confidence"] != "low"
            else " (low data confidence — treat as directional)"
        )
        bench_note = ""
        if top.get("benchmark"):
            bench_note = (
                f" — {top['benchmark']['ratio']}x the "
                f"{top_name} benchmark ({top['benchmark']['benchmark_pct']}% "
                "ER by followers)"
            )
        recs.append({
            "type": "channel_focus",
            "priority": "high",
            "platform": top_name,
            "text": (
                f"{top_name} is your strongest channel "
                f"({top['avg_engagement_rate']}% avg engagement over "
                f"{top['posts']} posts){bench_note}. Keep original content "
                f"here first; adapt it to secondary platforms after.{conf_note}"
            ),
        })

    weak = []
    for name, p in ranked[1:]:
        if (
            p["posts"] >= 3
            and p["impressions"] > 0  # zero impressions = data gap, not weak
            and _score(top) > 0
            and _score(p) / _score(top) < 0.4
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

    # 4. Momentum — rising/declining channels. Requires a minimum of real
    # events so 1→2 = "+100%" doesn't fire.
    for name, p in platforms.items():
        mom = p.get("momentum_7d_engagement_pct")
        recent, prior = p["engagement_7d"], p["engagement_prev_7d"]
        if mom is None or max(recent, prior) < _MOMENTUM_MIN_EVENTS:
            continue
        if mom >= 25:
            recs.append({
                "type": "momentum",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} engagement is up {mom}% week-over-week "
                    f"({recent} vs {prior} interactions) — increase cadence "
                    "now while the algorithm favors you."
                ),
            })
        elif mom <= -25:
            recs.append({
                "type": "momentum",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} engagement dropped {abs(mom)}% week-over-week "
                    f"({recent} vs {prior} interactions) — review the last "
                    "7 days' content mix before scheduling more."
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

    # 6. Ads guidance — boost proven organic winners, only where an ads
    # product actually exists.
    for name, p in platforms.items():
        bp = PLATFORM_BEST_PRACTICES.get(name)
        top_post = p.get("top_post")
        if not (bp and bp.get("ads_available") and top_post
                and p["posts"] >= 5 and top_post["engagement_rate"] > 0):
            continue
        demo = p.get("audience_demographics") or {}
        countries = demo.get("audience_country") or demo.get("country")
        targeting = ""
        if isinstance(countries, dict) and countries:
            top3 = sorted(countries.items(), key=lambda kv: kv[1], reverse=True)[:3]
            targeting = (" Top audience countries: "
                         + ", ".join(c for c, _ in top3) + ".")
        recs.append({
            "type": "ads",
            "priority": "low",
            "platform": name,
            "text": (
                f"{name}: {bp['ads']} Top organic post hit "
                f"{top_post['engagement_rate']}% ER — it's the boost "
                f"candidate if you run paid.{targeting}"
            ),
        })

    # 7. Cross-posting suggestion — main tier → secondary tier.
    main_name = next(
        (n for n, p in platforms.items() if p["focus_tier"] == "main"), None
    )
    if main_name and platforms[main_name]["posts"] > 0:
        secondary = [
            n for n, p in platforms.items() if p["focus_tier"] == "secondary"
        ]
        if secondary:
            recs.append({
                "type": "crosspost",
                "priority": "medium",
                "text": (
                    f"Repurpose your top {main_name} post to "
                    f"{', '.join(secondary)} — adapt format per platform "
                    "(carousel→IG carousel, short text→Threads). "
                    "Main-platform content leads; Meta gets adaptations."
                ),
            })

    # 8. Campaign synthesis — one concrete plan tying it together.
    if top["posts"] >= 3:
        bp = PLATFORM_BEST_PRACTICES.get(top_name, {})
        window = ""
        if top.get("best_weekday_athens") and top.get("best_hour_athens"):
            wd, _ = top["best_weekday_athens"]
            hr, _ = top["best_hour_athens"]
            window = f" on {_WEEKDAYS[wd]} ~{hr:02d}:00 Athens"
        fmt = "media/carousel" if (
            (top.get("media_avg_er") or 0) >= (top.get("text_avg_er") or 0)
        ) else "text-led"
        recs.append({
            "type": "campaign",
            "priority": "medium",
            "text": (
                f"Next sprint: {bp.get('cadence', 'steady cadence')} on "
                f"{top_name}{window}, {fmt} format. Adapt each post to "
                "secondary platforms; if a post beats your account median "
                "ER by 2x, that's the paid-amplification candidate."
            ),
        })

    return recs
