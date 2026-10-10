"""Unit tests for analytics_sync pure helpers + snapshot write paths.

Focuses on the logic that decides what lands in PostAnalyticsSnapshot /
FollowerSnapshot / AnalyticsEvent — the code that corrupted series before
(-98% follower deltas, repost cards carrying foreign counters, x_web
writing identical rows 11x in a row).
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
from app.services.analytics_sync import (
    MetricBundle,
    SyncResult,
    _alt_urn,
    _is_hard_stats_failure,
    _is_repost_scrape_text,
    _meta_unsupported_metric_names,
    _normalize_post_urn,
    _org_urn,
    _parse_share_stats_element,
    _persist_snapshot,
    _record_follower_snapshot,
    _restli_list,
    _urn_kind,
)

# ── Fake AsyncSession ────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def first(self):
        return self._v

    def scalar_one_or_none(self):
        return self._v


class _DB:
    """Queue-driven execute(); db.add/delete capture."""

    def __init__(self, results=()):
        self._q = deque(results)
        self.added: list = []
        self.deleted: list = []

    async def execute(self, stmt):
        return _Res(self._q.popleft() if self._q else None)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)


def _account(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform=kw.pop("platform", "linkedin"),
        account_id=kw.pop("account_id", "12345"),
        meta_data=kw.pop("meta_data", {}),
        **kw,
    )


# ── MetricBundle ─────────────────────────────────────────────────────


def test_engagement_sums_counters():
    b = MetricBundle(impressions=1000, clicks=10, likes=20, comments=5, shares=3)
    assert b.engagement == 38
    assert b.engagement_rate == pytest.approx(0.038)


def test_engagement_rate_zero_impressions():
    assert MetricBundle(likes=50).engagement_rate == 0.0
    assert MetricBundle(impressions=0, likes=1).engagement_rate == 0.0


# ── URN helpers ──────────────────────────────────────────────────────


def test_normalize_post_urn_passthrough_and_decode():
    assert _normalize_post_urn("urn:li:share:123") == "urn:li:share:123"
    # URL-encoded URNs are decoded before classification
    assert _normalize_post_urn("urn%3Ali%3AugcPost%3A99") == "urn:li:ugcPost:99"


def test_normalize_post_urn_bare_digits_rejected():
    # Bare numeric ids are ambiguous (share vs ugcPost) — must not guess
    assert _normalize_post_urn("7352000000000000000") is None
    assert _normalize_post_urn(None) is None
    assert _normalize_post_urn("  ") is None


def test_org_urn_prefers_meta_author_urn():
    acc = _account(meta_data={"author_urn": "urn:li:organization:777"}, account_id="999")
    assert _org_urn(acc) == "urn:li:organization:777"
    assert _org_urn(_account(account_id="999")) == "urn:li:organization:999"


def test_restli_list_encodes_urns_keeps_parens():
    out = _restli_list(["urn:li:share:1", "urn:li:ugcPost:2"])
    assert out.startswith("List(") and out.endswith(")")
    assert "urn%3Ali%3Ashare%3A1" in out


def test_urn_kind_and_alt():
    assert _urn_kind("urn:li:ugcPost:5") == "ugcPosts"
    assert _urn_kind("urn:li:share:5") == "shares"
    assert _urn_kind("urn:li:organization:5") is None
    assert _alt_urn("urn:li:ugcPost:5") == "urn:li:share:5"
    assert _alt_urn("urn:li:share:5") == "urn:li:ugcPost:5"
    assert _alt_urn("urn:li:organization:5") is None


# ── Share-stats parsing ──────────────────────────────────────────────


def test_parse_share_stats_element_maps_fields():
    urn, b = _parse_share_stats_element({
        "ugcPost": "urn:li:ugcPost:1",
        "totalShareStatistics": {
            "impressionCount": 100, "clickCount": 4, "likeCount": 7,
            "commentCount": 2, "shareCount": 1, "uniqueImpressionsCount": 80,
        },
    })
    assert urn == "urn:li:ugcPost:1"
    assert (b.impressions, b.clicks, b.likes, b.comments, b.shares, b.reach) == (100, 4, 7, 2, 1, 80)


def test_parse_share_stats_element_fallbacks():
    urn, b = _parse_share_stats_element({
        "share": "urn:li:share:2",
        "totalShareStatistics": {"uniqueImpressions": 40},
    })
    assert urn == "urn:li:share:2" and b.reach == 40 and b.impressions == 0


# ── Hard-failure classification ──────────────────────────────────────


def test_hard_stats_failure_rules():
    assert _is_hard_stats_failure(500, "") is True
    assert _is_hard_stats_failure(401, "token") is True
    assert _is_hard_stats_failure(403, "denied") is True
    # Stale local ids are SOFT — must not pollute digest warnings
    assert _is_hard_stats_failure(400, "Could not find entity") is False
    assert _is_hard_stats_failure(404, "ACTIVITYIDS missing") is False
    assert _is_hard_stats_failure(400, "something_not_found") is False
    # Other 4xx are hard; non-4xx non-5xx is not
    assert _is_hard_stats_failure(429, "rate") is True
    assert _is_hard_stats_failure(302, "") is False


def test_meta_unsupported_metric_names_all_phrasings():
    assert _meta_unsupported_metric_names(
        "does not support the impressions, replies metric for this media product type"
    ) == {"impressions", "replies"}
    assert _meta_unsupported_metric_names(
        "The impressions, replies metrics are not available for this media product type"
    ) == {"impressions", "replies"}
    assert _meta_unsupported_metric_names(
        "(#100) metric impressions is not available for this media product type"
    ) == {"impressions"}
    assert _meta_unsupported_metric_names("unrelated error") == set()


# ── Repost scrape guard ──────────────────────────────────────────────


def test_repost_scrape_text():
    assert _is_repost_scrape_text("Jane Doe reposted this") is True
    assert _is_repost_scrape_text("x reposted by y") is True
    assert _is_repost_scrape_text("Regular post content about cloud") is False
    assert _is_repost_scrape_text(None) is False
    assert _is_repost_scrape_text("") is False
    # Marker beyond the 300-char head window is not a repost card
    assert _is_repost_scrape_text("a" * 400 + " reposted this") is False


# ── _record_follower_snapshot ────────────────────────────────────────


@pytest.mark.asyncio
async def test_follower_snapshot_rejects_zero_and_negative():
    db = _DB()
    assert await _record_follower_snapshot(db, _account(), "linkedin", 0) is False
    assert await _record_follower_snapshot(db, _account(), "linkedin", -5) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_follower_snapshot_first_reading_written():
    db = _DB(results=[None])  # no prev row
    assert await _record_follower_snapshot(db, _account(), "linkedin", 66) is True
    snap = db.added[0]
    assert isinstance(snap, FollowerSnapshot) and snap.followers == 66


@pytest.mark.asyncio
async def test_follower_snapshot_implausible_rejected():
    # prev row (followers, captured_at) — 841 -> 952822 is the documented corruption
    db = _DB(results=[(841, datetime.now(UTC))])
    assert await _record_follower_snapshot(db, _account(), "linkedin", 952_822) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_follower_snapshot_unchanged_within_heartbeat_skipped():
    db = _DB(results=[(66, datetime.now(UTC) - timedelta(days=1))])
    assert await _record_follower_snapshot(db, _account(), "linkedin", 66) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_follower_snapshot_heartbeat_row_after_seven_days():
    db = _DB(results=[(66, datetime.now(UTC) - timedelta(days=8))])
    assert await _record_follower_snapshot(db, _account(), "linkedin", 66) is True


@pytest.mark.asyncio
async def test_follower_snapshot_real_delta_written():
    db = _DB(results=[(66, datetime.now(UTC))])
    assert await _record_follower_snapshot(db, _account(), "linkedin", 70) is True


# ── _persist_snapshot ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_snapshot_skips_unavailable():
    db = _DB()
    res = SyncResult()
    for note in ("stats_unavailable", "platform_deleted"):
        await _persist_snapshot(
            db, account=_account(), post_id=None, platform_post_id="x",
            metrics=MetricBundle(notes=note), captured_at=datetime.now(UTC),
            source="linkedin", result=res,
        )
    assert res.skipped == 2 and res.synced == 0 and db.added == []


@pytest.mark.asyncio
async def test_persist_snapshot_hard_failure_becomes_error():
    db = _DB()
    res = SyncResult()
    await _persist_snapshot(
        db, account=_account(), post_id=None, platform_post_id="urn:1",
        metrics=MetricBundle(notes="linkedin stats HTTP 401 expired token"),
        captured_at=datetime.now(UTC), source="linkedin", result=res,
    )
    assert res.errors and "urn:1" in res.errors[0] and res.skipped == 0


@pytest.mark.asyncio
async def test_persist_snapshot_soft_failure_skips():
    db = _DB()
    res = SyncResult()
    await _persist_snapshot(
        db, account=_account(), post_id=None, platform_post_id="urn:1",
        metrics=MetricBundle(notes="linkedin stats HTTP 400 could not find entity"),
        captured_at=datetime.now(UTC), source="linkedin", result=res,
    )
    assert res.skipped == 1 and not res.errors


@pytest.mark.asyncio
async def test_persist_snapshot_dedups_identical_metrics_24h():
    now = datetime.now(UTC)
    # latest_metrics row identical to incoming -> skipped
    db = _DB(results=[(100, 4, 7, 2, 1)])
    res = SyncResult()
    await _persist_snapshot(
        db, account=_account(), post_id=None, platform_post_id="urn:1",
        metrics=MetricBundle(impressions=100, clicks=4, likes=7, comments=2, shares=1),
        captured_at=now, source="x_web", result=res,
    )
    assert res.skipped == 1 and db.added == []


@pytest.mark.asyncio
async def test_persist_snapshot_repost_zeros_foreign_counters():
    now = datetime.now(UTC)
    # no dedup-note select (notes None), latest_metrics=None, then 5 counter selects -> None
    db = _DB(results=[None, None, None, None, None, None])
    res = SyncResult()
    await _persist_snapshot(
        db, account=_account(), post_id=None, platform_post_id="urn:repost",
        metrics=MetricBundle(
            impressions=9000, clicks=300, likes=500, comments=40, shares=10,
            raw={"is_repost": True},
        ),
        captured_at=now, source="linkedin_member", result=res,
    )
    assert res.synced == 1
    snap = next(o for o in db.added if isinstance(o, PostAnalyticsSnapshot))
    # The original author's counters must not become ours
    assert (snap.impressions, snap.likes, snap.clicks) == (0, 0, 0)
    # Zeroed counters add no analytics events
    assert not any(isinstance(o, AnalyticsEvent) for o in db.added)


@pytest.mark.asyncio
async def test_persist_snapshot_writes_metrics_and_counter_events():
    now = datetime.now(UTC)
    db = _DB(results=[None, None, None, None, None, None])
    res = SyncResult()
    await _persist_snapshot(
        db, account=_account(), post_id=None, platform_post_id="urn:ok",
        metrics=MetricBundle(impressions=100, clicks=4, likes=7, comments=2, shares=1),
        captured_at=now, source="linkedin_org", result=res,
    )
    assert res.synced == 1 and res.snapshots == ["urn:ok"]
    snap = next(o for o in db.added if isinstance(o, PostAnalyticsSnapshot))
    assert snap.engagement == 14 and snap.engagement_rate == pytest.approx(0.14)
    events = [o for o in db.added if isinstance(o, AnalyticsEvent)]
    assert len(events) == 5  # impression, click, like, comment, share
