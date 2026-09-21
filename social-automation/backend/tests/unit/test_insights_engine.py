"""Unit tests for the cross-platform insights engine.

compute_insights/_recommend/_window_delta are pure functions over plain rows —
tested here without a database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.insights_engine import (
    PLATFORM_BENCHMARKS,
    PLATFORM_BEST_PRACTICES,
    _confidence,
    _pct_change,
    _recommend,
    _window_delta,
    compute_insights,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _post_row(
    platform="linkedin",
    impressions=100,
    engagement=5,
    er=5.0,
    likes=3,
    comments=1,
    shares=1,
    notes=None,
    published_at=None,
    media=True,
):
    return SimpleNamespace(
        platform=platform,
        post_id=uuid.uuid4(),
        impressions=impressions,
        engagement=engagement,
        engagement_rate=er,
        likes=likes,
        comments=comments,
        shares=shares,
        notes=notes,
        published_at=published_at,
        created_at=published_at,
        media_ids=[uuid.uuid4()] if media else [],
    )


def _snap_row(platform, ppid, captured_at, engagement, impressions):
    return SimpleNamespace(
        platform=platform,
        platform_post_id=ppid,
        captured_at=captured_at,
        engagement=engagement,
        impressions=impressions,
    )


def _follower_row(platform, followers, captured_at, account_id=None):
    return SimpleNamespace(
        platform=platform,
        social_account_id=account_id or uuid.uuid4(),
        followers=followers,
        captured_at=captured_at,
    )


def _plat(**over):
    base = {
        "focus_tier": "secondary",
        "confidence": "medium",
        "posts": 10,
        "impressions": 1000,
        "engagement": 50,
        "likes": 30,
        "comments": 10,
        "shares": 10,
        "avg_engagement_rate": 5.0,
        "er_by_followers_pct": 1.0,
        "benchmark": None,
        "best_hour_athens": (10, 5.0),
        "best_weekday_athens": (2, 5.0),
        "media_avg_er": 6.0,
        "text_avg_er": 4.0,
        "momentum_7d_engagement_pct": None,
        "engagement_7d": 0,
        "engagement_prev_7d": 0,
        "follower_growth": None,
        "data_warnings": [],
        "account_insights": None,
        "audience_demographics": None,
        "top_post": {"post_id": "x", "engagement_rate": 8.0, "impressions": 200},
    }
    base.update(over)
    return base


# ── helpers ──────────────────────────────────────────────────────────────


def test_pct_change():
    assert _pct_change(10, 5) == 100.0
    assert _pct_change(5, 10) == -50.0
    assert _pct_change(5, 0) is None


def test_confidence_thresholds():
    assert _confidence(10, 500) == "high"
    assert _confidence(5, 100) == "medium"
    assert _confidence(9, 499) == "medium"
    assert _confidence(4, 9999) == "low"
    assert _confidence(10, 499) == "medium"  # impressions in mid band
    assert _confidence(10, 99) == "low"


def test_window_delta_uses_pre_window_baseline():
    # cumulative counters: 100 before window, 140 inside → 40 received
    pts = [
        (NOW - timedelta(days=8), 100, 1000),
        (NOW - timedelta(days=2), 140, 1500),
    ]
    eng, imp = _window_delta(pts, NOW - timedelta(days=7), NOW)
    assert eng == 40
    assert imp == 500


def test_window_delta_no_baseline_uses_first_in_window():
    pts = [(NOW - timedelta(days=2), 140, 1500)]
    eng, imp = _window_delta(pts, NOW - timedelta(days=7), NOW)
    assert eng == 0  # single point, nothing received measurably
    assert imp == 0


def test_window_delta_empty_window():
    pts = [(NOW - timedelta(days=10), 5, 50)]
    assert _window_delta(pts, NOW - timedelta(days=7), NOW) == (0, 0)


def test_window_delta_clamps_negative():
    pts = [
        (NOW - timedelta(days=8), 100, 1000),
        (NOW - timedelta(days=2), 90, 900),  # counter reset/deletion
    ]
    assert _window_delta(pts, NOW - timedelta(days=7), NOW) == (0, 0)


# ── compute_insights aggregation ─────────────────────────────────────────


def test_compute_aggregates_per_platform():
    rows = [
        _post_row("linkedin", impressions=200, engagement=10, er=5.0,
                  published_at=NOW - timedelta(days=3)),
        _post_row("linkedin", impressions=300, engagement=20, er=6.6,
                  published_at=NOW - timedelta(days=5), media=False),
        _post_row("threads", impressions=100, engagement=1, er=1.0,
                  published_at=NOW - timedelta(days=4)),
    ]
    out = compute_insights(rows, [], [], [], now=NOW)
    li = out["platforms"]["linkedin"]
    assert li["posts"] == 2
    assert li["impressions"] == 500
    assert li["engagement"] == 30
    assert li["avg_engagement_rate"] == 6.0
    assert li["media_avg_er"] == 5.0
    assert li["text_avg_er"] == 6.6
    assert out["platforms"]["threads"]["posts"] == 1


def test_compute_timing_buckets_athens():
    # 23:30 UTC = 02:30 next day Athens (EEST, UTC+3) — weekday must shift too
    pub = datetime(2026, 9, 17, 23, 30, tzinfo=UTC)  # Thu UTC → Fri Athens
    rows = [
        _post_row(published_at=pub, er=4.0),
        _post_row(published_at=pub, er=6.0),
    ]
    out = compute_insights(rows, [], [], [], now=NOW)
    li = out["platforms"]["linkedin"]
    assert li["best_hour_athens"] == (2, 5.0)
    assert li["best_weekday_athens"] == (4, 5.0)  # Friday in Athens


def test_compute_momentum_from_snapshot_deltas():
    # one post: 100→140 in prior week, 140→170 in recent week
    delta_rows = [
        _snap_row("linkedin", "urn:1", NOW - timedelta(days=13), 90, 900),
        _snap_row("linkedin", "urn:1", NOW - timedelta(days=8), 100, 1000),
        _snap_row("linkedin", "urn:1", NOW - timedelta(days=2), 140, 1400),
        _snap_row("linkedin", "urn:1", NOW - timedelta(days=1), 170, 1800),
    ]
    rows = [_post_row(published_at=NOW - timedelta(days=30))]
    out = compute_insights(rows, delta_rows, [], [], now=NOW)
    li = out["platforms"]["linkedin"]
    # prior window: baseline 90 → last-in-window 100 = +10
    # recent window: baseline 100 (pre-window) → 170 = +70
    assert li["engagement_prev_7d"] == 10
    assert li["engagement_7d"] == 70
    assert li["momentum_7d_engagement_pct"] == 600.0


def test_compute_follower_growth_and_benchmark():
    rows = [_post_row("instagram", impressions=500, engagement=30, er=6.0)
            for _ in range(10)]
    acct = uuid.uuid4()
    followers = [
        _follower_row("instagram", 1000, NOW - timedelta(days=30), acct),
        _follower_row("instagram", 1250, NOW - timedelta(days=1), acct),
    ]
    out = compute_insights(rows, [], followers, [], now=NOW)
    ig = out["platforms"]["instagram"]
    assert ig["follower_growth"]["net"] == 250
    assert ig["follower_growth"]["current"] == 1250
    # 300 total eng / 10 posts = 30 per post ÷ 1250 followers = 2.4%
    # vs tech benchmark 0.33% → well above
    assert ig["er_by_followers_pct"] == 2.4
    assert ig["benchmark"]["verdict"] == "above_benchmark"
    assert ig["benchmark"]["benchmark_pct"] == 0.33


def test_compute_data_warnings_collected():
    rows = [_post_row("instagram", impressions=0, engagement=0,
                      notes="insights_scope_missing — reconnect")]
    out = compute_insights(rows, [], [], [], now=NOW)
    assert "insights_scope_missing — reconnect" in (
        out["platforms"]["instagram"]["data_warnings"])


def test_compute_no_benchmark_without_followers():
    rows = [_post_row("instagram", engagement=10)]
    out = compute_insights(rows, [], [], [], now=NOW)
    assert out["platforms"]["instagram"]["benchmark"] is None
    assert out["platforms"]["instagram"]["er_by_followers_pct"] is None


def test_compute_events_pick_latest_per_type():
    rows = [_post_row()]
    evs = [
        ("instagram", "account_insights", {"reach": 500}),      # newest first
        ("instagram", "account_insights", {"reach": 300}),
        ("instagram", "audience_demographics", {"country": {"GR": 60}}),
    ]
    out = compute_insights(rows, [], [], evs, now=NOW)
    ig = out["platforms"]["instagram"]
    assert ig["account_insights"] == {"reach": 500}
    assert ig["audience_demographics"] == {"country": {"GR": 60}}


# ── _recommend ───────────────────────────────────────────────────────────


def test_recommend_empty():
    recs = _recommend({})
    assert recs[0]["type"] == "setup"


def test_recommend_channel_focus_with_benchmark():
    plats = {
        "linkedin": _plat(
            focus_tier="main", engagement=80, posts=10,
            benchmark={
                "ratio": 1.5, "benchmark_pct": 3.6,
                "your_er_by_followers_pct": 5.4,
                "verdict": "above_benchmark",
            },
        ),
        "threads": _plat(engagement=5, posts=10),
    }
    recs = _recommend(plats)
    focus = next(r for r in recs if r["type"] == "channel_focus")
    assert focus["platform"] == "linkedin"
    assert "interactions/post" in focus["text"]
    assert "above benchmark" in focus["text"]


def test_benchmark_gated_on_min_followers():
    # 2 engagements over 2 posts on a 9-follower account must not produce
    # a fake ~11% ER-by-followers verdict
    acct = uuid.uuid4()
    rows = [_post_row("threads", engagement=1) for _ in range(2)]
    followers = [
        _follower_row("threads", 9, NOW - timedelta(days=30), acct),
        _follower_row("threads", 9, NOW - timedelta(days=1), acct),
    ]
    out = compute_insights(rows, [], followers, [], now=NOW)
    assert out["platforms"]["threads"]["er_by_followers_pct"] is None
    assert out["platforms"]["threads"]["benchmark"] is None


def test_recommend_deprioritize_merged_and_excludes_data_gaps():
    plats = {
        "linkedin": _plat(focus_tier="main", avg_engagement_rate=10.0, posts=10),
        # weak: has impressions but low engagement
        "threads": _plat(avg_engagement_rate=1.0, engagement=3, posts=5),
        # zero impressions = data gap, must NOT be deprioritized
        "instagram": _plat(avg_engagement_rate=0.0, engagement=0,
                           impressions=0, posts=5,
                           data_warnings=["insights_scope_missing"]),
    }
    recs = _recommend(plats)
    deps = [r for r in recs if r["type"] == "deprioritize"]
    assert len(deps) == 1
    assert "threads" in deps[0]["platform"]
    assert "instagram" not in deps[0]["platform"]
    # and instagram gets a data_gap rec instead
    assert any(r["type"] == "data_gap" and r["platform"] == "instagram"
               for r in recs)


def test_recommend_timing_only_with_engagement_signal():
    plats = {
        "linkedin": _plat(focus_tier="main", posts=10,
                          best_weekday_athens=(2, 5.0), best_hour_athens=(9, 4.0)),
        "threads": _plat(posts=5,
                         best_weekday_athens=(2, 0.0), best_hour_athens=(9, 0.0)),
    }
    recs = _recommend(plats)
    li = next(r for r in recs if r["type"] == "timing" and r["platform"] == "linkedin")
    assert li["priority"] == "medium"
    assert "Wed" in li["text"] and "09:00" in li["text"]
    th = next(r for r in recs if r["type"] == "timing" and r["platform"] == "threads")
    assert th["priority"] == "low"
    assert "baseline" in th["text"]


def test_recommend_momentum_floor_blocks_small_numbers():
    plats = {
        "linkedin": _plat(focus_tier="main", posts=10,
                          momentum_7d_engagement_pct=100.0,
                          engagement_7d=2, engagement_prev_7d=1),
    }
    assert not any(r["type"] == "momentum" for r in _recommend(plats))
    plats["linkedin"]["engagement_7d"] = 20
    plats["linkedin"]["engagement_prev_7d"] = 10
    assert any(r["type"] == "momentum" for r in _recommend(plats))


def test_recommend_momentum_fires_on_zero_prior():
    # prior=0 → pct_change is None but the signal is real
    plats = {
        "linkedin": _plat(focus_tier="main", posts=10,
                          momentum_7d_engagement_pct=None,
                          engagement_7d=10, engagement_prev_7d=0),
    }
    recs = _recommend(plats)
    assert any(r["type"] == "momentum" and r["platform"] == "linkedin"
               for r in recs)


def test_recommend_ads_gating_and_demographics():
    plats = {
        # threads has no ads product — excluded even with a top post
        "threads": _plat(posts=10),
        "instagram": _plat(
            posts=10, engagement=50,
            audience_demographics={"audience_country": {"GR": 60, "US": 20}},
        ),
        "linkedin": _plat(focus_tier="main", posts=10),
    }
    recs = _recommend(plats)
    ads = {r["platform"] for r in recs if r["type"] == "ads"}
    assert "threads" not in ads
    assert "instagram" in ads and "linkedin" in ads
    ig_ad = next(r for r in recs if r["type"] == "ads" and r["platform"] == "instagram")
    assert "GR" in ig_ad["text"]


def test_recommend_crosspost_uses_actual_main_platform():
    plats = {
        "linkedin": _plat(focus_tier="main", posts=10),
        "instagram": _plat(focus_tier="secondary", posts=5),
    }
    recs = _recommend(plats)
    cp = next(r for r in recs if r["type"] == "crosspost")
    assert "linkedin" in cp["text"]
    assert "instagram" in cp["text"]


def test_recommend_campaign_synthesis():
    plats = {"linkedin": _plat(focus_tier="main", posts=10)}
    recs = _recommend(plats)
    assert any(r["type"] == "campaign" for r in recs)


def test_recommend_low_confidence_flagged():
    plats = {"linkedin": _plat(focus_tier="main", posts=5, confidence="low")}
    recs = _recommend(plats)
    focus = next(r for r in recs if r["type"] == "channel_focus")
    assert "low data confidence" in focus["text"]


# ── benchmark table sanity ────────────────────────────────────────────────


def test_benchmarks_cover_all_platforms():
    for name in ("linkedin", "instagram", "facebook", "threads",
                 "twitter", "tiktok"):
        assert name in PLATFORM_BENCHMARKS
        assert PLATFORM_BENCHMARKS[name]["er_by_followers_pct"] > 0
        assert name in PLATFORM_BEST_PRACTICES
