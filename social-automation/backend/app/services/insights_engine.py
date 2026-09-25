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

import statistics
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
        # Prefer same-methodology ER (Socialinsider). Hootsuite Tech "3.60"
        # is avg engagement/post (absolute), not ER% — do not use as industry_pct.
        "industry_pct": None,
        "per_format_pct": {
            "native_document": 7.00, "multi_image": 6.45, "video": 6.00,
            "image": 5.30, "text": 4.50, "poll": 4.20, "link": 3.25,
        },
        "source": "Socialinsider 2025 (1.3M business posts)",
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
# Covers the signatures the collectors actually emit: HTTP error notes,
# missing scopes/products, unavailable endpoints, and dead platform objects.
_DATA_GAP_NOTES = (
    "scope_missing",
    "stats_unavailable",
    "missing_stats",
    "not_implemented",
    "not_found",
    "quota",
    "session expired",
    "token",
    "HTTP 4",
    "HTTP 5",
)

# Minimum evidence before momentum recommendations fire (suppresses
# small-number noise like 1→2 engagements = +100%).
_MOMENTUM_MIN_EVENTS = 5

# ER-by-followers needs a meaningful denominator — below this, tiny follower
# counts amplify one like into a double-digit "rate".
_BENCHMARK_MIN_FOLLOWERS = 50

# Outlier detection (Tukey IQR fences). Social metrics are heavily
# zero-inflated — when IQR collapses to 0 every nonzero post would flag, so
# the fallback fence requires an absolute jump worth noticing.
_MIN_POSTS_FOR_OUTLIERS = 4
_IQR_FALLBACK_MIN_VIRAL = 5

# Follower-series anomaly: flag a single-step change this large
# (absolute floor or 5% of current count — catches bot spikes and
# unfollow storms without flagging normal growth noise).
_FOLLOWER_ANOMALY_MIN = 10
_FOLLOWER_ANOMALY_PCT = 0.05

# Counter resets per platform before it becomes a reportable data issue.
_COUNTER_RESET_WARN_THRESHOLD = 3


def _sanitize_metric(value: Any) -> tuple[int, bool]:
    """Clamp a metric to a sane non-negative int.

    Returns (clean_value, was_repaired). Platform APIs occasionally return
    negative or non-numeric values mid-outage; they would silently corrupt
    every aggregate, so repair + count instead of trusting them.
    """
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return 0, True
    if v < 0:
        return 0, True
    return v, False


def _iqr_outliers(values: list[float]) -> dict[int, str]:
    """index → 'viral' | 'underperformer' via Tukey fences on raw values.

    With a collapsed IQR (mostly-zero engagement), the fallback upper fence
    `max(3*q3, q3+5)` keeps only genuinely exceptional spikes — a post at
    6+ engagements while the top quartile sits at 0 is a real outlier.
    """
    if len(values) < _MIN_POSTS_FOR_OUTLIERS:
        return {}
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    # Absolute floor on the upper fence: on near-zero platforms the IQR is
    # tiny and a single like would flag as "viral". 1 engagement is noise;
    # 6+ over a flat series is a real spike.
    upper = q3 + max(1.5 * iqr, _IQR_FALLBACK_MIN_VIRAL)
    lower = q1 - 1.5 * iqr
    out: dict[int, str] = {}
    for i, v in enumerate(values):
        if v > upper:
            out[i] = "viral"
        elif iqr > 0 and v < lower:
            out[i] = "underperformer"
    return out


def _count_resets(points: list[tuple[datetime, int, int]]) -> int:
    """Times a cumulative engagement counter decreased between captures —
    signals post deletion, stat correction, or API weirdness."""
    return sum(
        1
        for prev, cur in zip(points, points[1:], strict=False)
        if cur[1] < prev[1]
    )


def _follower_anomaly(series: list[tuple[datetime, int]]) -> dict[str, Any] | None:
    """Largest single-step follower change that beats the anomaly floor."""
    worst: dict[str, Any] | None = None
    for (prev_at, prev_n), (at, n) in zip(series, series[1:], strict=False):
        delta = n - prev_n
        floor = max(_FOLLOWER_ANOMALY_MIN, int(abs(n) * _FOLLOWER_ANOMALY_PCT))
        if abs(delta) >= floor and (worst is None or abs(delta) > abs(worst["delta"])):
            worst = {"delta": delta, "at": at.isoformat()}
    return worst


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
            "er_imp": 0,   # impressions on rows that have a denominator
            "er_eng": 0,   # engagement on those same rows only
            "notes": set(),
            "top_post": None,
            "post_engagements": [],   # (post_id, engagement, er) — for outliers/medians
            "sanitized_rows": 0,
        }
    )
    for r in post_rows:
        p = plat[r.platform]
        er = float(r.engagement_rate or 0)
        if er < 0:
            er = 0.0
            p["sanitized_rows"] += 1
        impressions, repaired = _sanitize_metric(r.impressions)
        p["sanitized_rows"] += repaired
        engagement, repaired = _sanitize_metric(r.engagement)
        p["sanitized_rows"] += repaired
        likes, repaired = _sanitize_metric(r.likes)
        p["sanitized_rows"] += repaired
        comments, repaired = _sanitize_metric(r.comments)
        p["sanitized_rows"] += repaired
        shares, repaired = _sanitize_metric(r.shares)
        p["sanitized_rows"] += repaired
        p["posts"] += 1
        p["impressions"] += impressions
        p["engagement"] += engagement
        if impressions > 0:
            # ER-by-impressions needs a real denominator — posts whose source
            # can't report impressions (member-profile scrapes, quota-dead
            # APIs) would otherwise inflate the rate with free engagement.
            p["er_imp"] += impressions
            p["er_eng"] += engagement
        p["likes"] += likes
        p["comments"] += comments
        p["shares"] += shares
        p["post_engagements"].append((str(r.post_id), engagement, er))
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
    counter_resets: dict[str, int] = defaultdict(int)
    for key, pts in snap_series.items():
        pts.sort(key=lambda p: p[0])
        counter_resets[key.split(":", 1)[0]] += _count_resets(pts)

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
        # A 0 reading is a failed scrape, not a real count — dropping it keeps
        # a first real count from masquerading as a massive gain.
        if int(fr.followers) <= 0:
            continue
        follower_windows[f"{fr.platform}:{fr.social_account_id}"].append(
            (fr.captured_at, int(fr.followers))
        )
    follower_growth: dict[str, dict[str, Any]] = {}
    for key, series in follower_windows.items():
        platform = key.split(":", 1)[0]
        first, last = series[0][1], series[-1][1]
        g = follower_growth.setdefault(
            platform, {"first": 0, "last": 0, "accounts": 0, "anomaly": None}
        )
        g["first"] += first
        g["last"] += last
        g["accounts"] += 1
        anomaly = _follower_anomaly(series)
        if anomaly and (g["anomaly"] is None or abs(anomaly["delta"]) > abs(g["anomaly"]["delta"])):
            g["anomaly"] = anomaly

    # ── Latest account-insight events per platform ─────────────────────────
    latest_events: dict[tuple[str, str], dict] = {}
    for platform, event_type, meta in ev_rows:
        latest_events.setdefault((platform, event_type), meta or {})

    # ── Compose per-platform report ────────────────────────────────────────
    platforms: dict[str, dict[str, Any]] = {}
    for name, p in plat.items():
        posts = p["posts"]
        avg_er = round(p["er_eng"] / p["er_imp"] * 100, 2) if p["er_imp"] else 0.0

        def _best(buckets: dict[int, list[int]], min_n: int = 2):
            """Winning bucket's avg ER must beat the platform mean ER by a
            margin — otherwise the argmax is noise ordering, not a signal."""
            cands = [
                (k, round(v[1] / v[0], 2)) for k, v in buckets.items() if v[0] >= min_n
            ]
            if not cands:
                return None
            total_n = sum(v[0] for v in buckets.values())
            mean_er = sum(v[1] for v in buckets.values()) / total_n if total_n else 0.0
            win = max(cands, key=lambda kv: kv[1])
            return win if win[1] > 0 and win[1] >= mean_er * 1.25 else None

        media_er = (
            round(p["media_posts"][1] / p["media_posts"][0], 2) if p["media_posts"][0] else None
        )
        text_er = (
            round(p["text_posts"][1] / p["text_posts"][0], 2) if p["text_posts"][0] else None
        )
        mom = momentum.get(name, {})

        growth = follower_growth.get(name)
        followers_now = growth["last"] if growth else None

        # Robust per-post stats — a single viral post shouldn't define the
        # average. Medians power the benchmark comparison (Rival IQ's
        # published method is "median interactions/post ÷ followers").
        per_post = [e for _, e, _ in p["post_engagements"]]
        median_eng_post = round(statistics.median(per_post), 2) if per_post else 0.0
        median_er = (
            round(statistics.median([er for _, _, er in p["post_engagements"]]), 2)
            if p["post_engagements"]
            else 0.0
        )

        # Post-level outliers on raw engagement (IQR fences).
        flags = _iqr_outliers(per_post)
        outliers = [
            {
                "post_id": p["post_engagements"][i][0],
                "kind": flags[i],
                "engagement": p["post_engagements"][i][1],
                "engagement_rate": p["post_engagements"][i][2],
                "platform_median_engagement": median_eng_post,
            }
            for i in sorted(flags)
        ]

        # ER by followers — median interactions/post ÷ current followers,
        # the methodology every published benchmark uses. Gated on a
        # meaningful follower count: with ~10 followers, 2 likes reads as
        # a fake 20% "rate".
        er_by_followers = (
            round(median_eng_post / followers_now * 100, 3)
            if posts and followers_now and followers_now >= _BENCHMARK_MIN_FOLLOWERS
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

        # Timing buckets need a real sample — a 2-post bucket's ER max is
        # noise, not an audience signal.
        best_hr = _best(p["hours"], min_n=3)
        best_wd = _best(p["weekdays"], min_n=3)

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
            "median_engagement_rate": median_er,
            "median_engagement_per_post": median_eng_post,
            "er_by_followers_pct": er_by_followers,
            "benchmark": benchmark,
            "outliers": outliers,
            "best_hour_athens": best_hr,
            "best_weekday_athens": best_wd,
            "best_hour_sample": p["hours"][best_hr[0]][0] if best_hr else 0,
            "best_weekday_sample": p["weekdays"][best_wd[0]][0] if best_wd else 0,
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
                    "anomaly": growth["anomaly"],
                }
                if growth
                else None
            ),
            "data_warnings": data_warnings,
            "data_quality": {
                "snapshots": posts,
                "sanitized_rows": p["sanitized_rows"],
                "counter_resets": counter_resets.get(name, 0),
                "outlier_posts": len(outliers),
            },
            "account_insights": latest_events.get((name, "account_insights")),
            "audience_demographics": latest_events.get((name, "audience_demographics")),
        }

    # Platforms with account-level data but no tracked posts in the window
    # still belong in the report (e.g. TikTok followers via sidecar).
    for name in {
        *(n for n in follower_growth),
        *(p for p, _ in latest_events),
    } - platforms.keys():
        growth = follower_growth.get(name)
        platforms[name] = {
            "focus_tier": PLATFORM_BEST_PRACTICES.get(name, {}).get("focus", "last"),
            "confidence": "low",
            "posts": 0, "impressions": 0, "engagement": 0,
            "likes": 0, "comments": 0, "shares": 0,
            "avg_engagement_rate": 0.0,
            "median_engagement_rate": 0.0,
            "median_engagement_per_post": 0.0,
            "er_by_followers_pct": None,
            "benchmark": None,
            "outliers": [],
            "best_hour_athens": None,
            "best_weekday_athens": None,
            "best_hour_sample": 0,
            "best_weekday_sample": 0,
            "media_avg_er": None, "text_avg_er": None,
            "momentum_7d_engagement_pct": None,
            "momentum_7d_impressions_pct": None,
            "engagement_7d": 0, "engagement_prev_7d": 0,
            "follower_growth": (
                {
                    "net": growth["last"] - growth["first"],
                    "current": growth["last"],
                    "accounts": growth["accounts"],
                    "anomaly": growth["anomaly"],
                }
                if growth else None
            ),
            "data_warnings": [],
            "data_quality": {
                "snapshots": 0,
                "sanitized_rows": 0,
                "counter_resets": counter_resets.get(name, 0),
                "outlier_posts": 0,
            },
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
        "preprocessing": {
            "sanitized_rows": sum(
                (p.get("data_quality") or {}).get("sanitized_rows", 0)
                for p in platforms.values()
            ),
            "counter_resets": {
                n: r for n, r in counter_resets.items() if r
            },
            "outlier_posts": sum(
                (p.get("data_quality") or {}).get("outlier_posts", 0)
                for p in platforms.values()
            ),
        },
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

    # Rank by avg engagements per post — the only cross-platform metric that
    # survives broken denominators (LinkedIn impressions are under-reported,
    # Threads followers are too few for a rate). Rate-based verdicts are still
    # reported per-platform via `benchmark`.
    def _score(p: dict[str, Any]) -> float:
        return p["engagement"] / p["posts"] if p["posts"] else 0.0

    ranked = sorted(platforms.items(), key=lambda kv: _score(kv[1]), reverse=True)

    # 0. Data gaps first — fix collection before trusting any other signal.
    for name, p in platforms.items():
        for w in p.get("data_warnings", []):
            if "insights_scope_missing" in w:
                text = (
                    f"{name} insights are permission-blocked — reconnect the "
                    f"account so it grants the insights scope ({w})."
                )
            elif "quota_exhausted" in w:
                text = (
                    f"{name} API read quota is exhausted — metrics will resume "
                    "when the quota resets or the plan is upgraded."
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
            b = top["benchmark"]
            # LinkedIn (and any platform without industry_pct) compares against
            # the published ER-by-followers baseline, not a mismatched absolute.
            label = "industry median" if PLATFORM_BENCHMARKS.get(top_name, {}).get("industry_pct") else "platform baseline"
            bench_note = (
                f" — ER by followers {b['your_er_by_followers_pct']}% vs "
                f"{b['benchmark_pct']}% {label} "
                f"({b['verdict'].replace('_', ' ')})"
            )
        recs.append({
            "type": "channel_focus",
            "priority": "high",
            "platform": top_name,
            "text": (
                f"{top_name} is your strongest channel "
                f"({round(_score(top), 2)} avg interactions/post over "
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
            # Below ~5 posts per winning bucket the max-ER pick is the
            # winner's curse — report it but flag as directional, like the
            # channel_focus rec does for low-confidence platforms.
            thin = (
                min(
                    p.get("best_hour_sample") or 0,
                    p.get("best_weekday_sample") or 0,
                ) < 5
                or p.get("confidence") == "low"
            )
            conf_note = (
                "" if not thin
                else " (low data confidence — treat as directional)"
            )
            recs.append({
                "type": "timing",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"Your {name} audience engages most on {_WEEKDAYS[wd]} "
                    f"around {hr:02d}:00 Athens time ({wd_er}% / {hr_er}% avg "
                    f"ER in those buckets). Schedule there; platform baseline: "
                    f"{bp['best_windows'] if bp else 'n/a'}.{conf_note}"
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
        if max(recent, prior) < _MOMENTUM_MIN_EVENTS:
            continue
        if mom is None and recent >= _MOMENTUM_MIN_EVENTS:
            recs.append({
                "type": "momentum",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} received {recent} interactions in the last 7 "
                    "days after none the week before — momentum is building, "
                    "post again this week to compound it."
                ),
            })
        elif mom is not None and mom >= 25:
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
        elif mom is not None and mom <= -25:
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

    # 5. Outliers — viral posts are the strongest "replicate this" and
    # paid-boost signal the data can give; follower anomalies flag bot
    # purges or viral hits that distort trend lines.
    for name, p in platforms.items():
        ads_ok = PLATFORM_BEST_PRACTICES.get(name, {}).get("ads_available")
        for o in (p.get("outliers") or []):
            if o["kind"] != "viral":
                continue
            median = o.get("platform_median_engagement") or 0
            multiple = (
                f" — {round(o['engagement'] / median, 1)}× your median"
                if median else ""
            )
            recs.append({
                "type": "outlier",
                "priority": "high" if p["focus_tier"] == "main" else "medium",
                "platform": name,
                "text": (
                    f"{name} post {str(o['post_id'])[:8]} is a positive "
                    f"outlier: {o['engagement']} interactions{multiple}. "
                    "Replicate its topic/format next sprint"
                    + ("; it's also your best organic-proven boost candidate."
                       if ads_ok else ".")
                ),
            })
        g = p.get("follower_growth") or {}
        anomaly = g.get("anomaly")
        if anomaly:
            direction = "gained" if anomaly["delta"] > 0 else "lost"
            recs.append({
                "type": "follower_anomaly",
                "priority": "medium",
                "platform": name,
                "text": (
                    f"{name} {direction} {abs(anomaly['delta'])} followers in "
                    f"a single sync step ({str(anomaly['at'])[:10]}) — "
                    "unusual movement; check for a viral hit, bot cleanup, "
                    "or platform purge before trusting trend lines."
                ),
            })
        dq = p.get("data_quality") or {}
        issues = []
        if dq.get("sanitized_rows"):
            issues.append(f"{dq['sanitized_rows']} repaired metric values")
        if dq.get("counter_resets", 0) >= _COUNTER_RESET_WARN_THRESHOLD:
            issues.append(f"{dq['counter_resets']} counter resets")
        if issues:
            recs.append({
                "type": "data_gap",
                "priority": "low",
                "platform": name,
                "text": (
                    f"{name} preprocessing cleaned "
                    f"{' and '.join(issues)} — scores already exclude them."
                ),
            })

    # 5b. Follower growth signals.
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
            window = f", best window {_WEEKDAYS[wd]} ~{hr:02d}:00 Athens"
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
