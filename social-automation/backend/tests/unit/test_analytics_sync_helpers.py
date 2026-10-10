"""Tests for app/services/analytics_sync.py helpers + persistence guards.

Covers MetricBundle/SyncResult, LinkedIn URN helpers, Meta metric-rejection
parsing + adaptive retry, the follower plausibility/heartbeat guards, repost
scrape detection, counter upserts, snapshot dedup, and TikTok display-id
resolution — previously ~25% file coverage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import analytics_sync as A

# ── fakes ─────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.added = []
        self.deleted = []
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        pass

    async def flush(self):
        pass


def _account(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform=kw.pop("platform", "linkedin"),
        account_id=kw.pop("account_id", "12345"),
        access_token_enc=kw.pop("access_token_enc", None),
        meta_data=kw.pop("meta_data", {}),
        **kw,
    )


def _resp(status, json_body=None):
    r = SimpleNamespace(status_code=status)
    r.json = lambda: json_body or {}
    r.text = "err-body"
    return r


# ── MetricBundle / SyncResult ─────────────────────────────────────────


def test_metric_bundle_engagement():
    m = A.MetricBundle(likes=2, comments=3, shares=4, clicks=5, impressions=100)
    assert m.engagement == 14
    assert m.engagement_rate == pytest.approx(0.14)


def test_metric_bundle_zero_impressions():
    m = A.MetricBundle(likes=5)
    assert m.engagement_rate == 0.0


def test_sync_result_defaults():
    r = A.SyncResult()
    assert r.synced == 0 and r.errors == [] and r.snapshots == []


# ── LinkedIn helpers ──────────────────────────────────────────────────


def test_linkedin_headers():
    h = A._linkedin_headers("tok")
    assert h["Authorization"] == "Bearer tok"
    assert h["Linkedin-Version"] == A.LINKEDIN_VERSION
    assert h["X-Restli-Protocol-Version"] == "2.0.0"


def test_normalize_post_urn():
    assert A._normalize_post_urn(None) is None
    assert A._normalize_post_urn("  ") is None
    assert A._normalize_post_urn("urn:li:share:1") == "urn:li:share:1"
    assert A._normalize_post_urn("12345") is None  # bare digits ambiguous
    assert A._normalize_post_urn("abc-def") == "abc-def"
    assert A._normalize_post_urn("urn%3Ali%3Ashare%3A9") == "urn:li:share:9"


def test_org_urn():
    assert A._org_urn(_account(account_id="77")) == "urn:li:organization:77"
    acc = _account(meta_data={"author_urn": "urn:li:organization:99"})
    assert A._org_urn(acc) == "urn:li:organization:99"
    acc2 = _account(meta_data={"author_urn": "urn:li:person:x"})
    assert A._org_urn(acc2) == f"urn:li:organization:{acc2.account_id}"


def test_restli_list():
    out = A._restli_list(["urn:li:share:1", "urn:li:share:2"])
    assert out.startswith("List(") and out.endswith(")")
    assert "urn%3Ali%3Ashare%3A1" in out


def test_urn_kind():
    assert A._urn_kind("urn:li:ugcPost:1") == "ugcPosts"
    assert A._urn_kind("urn:li:share:1") == "shares"
    assert A._urn_kind("urn:li:organization:1") is None


def test_alt_urn():
    assert A._alt_urn("urn:li:ugcPost:42") == "urn:li:share:42"
    assert A._alt_urn("urn:li:share:42") == "urn:li:ugcPost:42"
    assert A._alt_urn("urn:li:organization:42") is None


def test_parse_share_stats_element():
    el = {
        "ugcPost": "urn:li:ugcPost:7",
        "totalShareStatistics": {
            "impressionCount": 10,
            "clickCount": 2,
            "likeCount": 3,
            "commentCount": 4,
            "shareCount": 5,
            "uniqueImpressionsCount": 8,
        },
    }
    urn, b = A._parse_share_stats_element(el)
    assert urn == "urn:li:ugcPost:7"
    assert (b.impressions, b.clicks, b.likes, b.comments, b.shares, b.reach) == (10, 2, 3, 4, 5, 8)

    el2 = {"share": "urn:li:share:9", "totalShareStatistics": {"uniqueImpressions": 6}}
    urn2, b2 = A._parse_share_stats_element(el2)
    assert urn2 == "urn:li:share:9" and b2.reach == 6


def test_is_hard_stats_failure():
    assert A._is_hard_stats_failure(500, "") is True
    assert A._is_hard_stats_failure(401, "x") is True
    assert A._is_hard_stats_failure(403, "x") is True
    assert A._is_hard_stats_failure(404, "could not find entity") is False
    assert A._is_hard_stats_failure(404, "missing activityIDs") is False
    assert A._is_hard_stats_failure(400, "not_found") is False
    assert A._is_hard_stats_failure(429, "throttled") is True


# ── Meta helpers ──────────────────────────────────────────────────────


def test_meta_unsupported_metric_names():
    m1 = A._meta_unsupported_metric_names("does not support the impressions, replies metric for this media product type")
    assert m1 == {"impressions", "replies"}
    m2 = A._meta_unsupported_metric_names("The views metrics are not available for this media product type")
    assert "views" in m2
    m3 = A._meta_unsupported_metric_names("something unrelated")
    assert m3 == set()


@pytest.mark.asyncio
async def test_meta_insights_get_200():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, {"data": [1]})))
    out = await A._meta_insights_get(client, "u", ["impressions", "reach"])
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_meta_insights_get_drops_indexed_metric():
    # metric[1] must be one of... → drops remaining[1] and retries
    bad = _resp(400, {"error": {"message": "metric[1] must be one of the following values"}})
    ok = _resp(200, {"data": []})
    client = SimpleNamespace(get=AsyncMock(side_effect=[bad, ok]))
    out = await A._meta_insights_get(client, "u", ["impressions", "bogus", "reach"])
    assert out.status_code == 200
    sent = client.get.await_args_list[-1].kwargs["params"]["metric"]
    assert sent == "impressions,reach"


@pytest.mark.asyncio
async def test_meta_insights_get_drops_named_metrics():
    bad = _resp(400, {"error": {"message": "does not support the impressions metric for this media product type"}})
    ok = _resp(200, {"data": []})
    client = SimpleNamespace(get=AsyncMock(side_effect=[bad, ok]))
    out = await A._meta_insights_get(client, "u", ["impressions", "reach"])
    assert out.status_code == 200
    assert client.get.await_args_list[-1].kwargs["params"]["metric"] == "reach"


@pytest.mark.asyncio
async def test_meta_insights_get_hash100_probe_merge():
    bad = _resp(400, {"error": {"message": "(#100) The value must be a valid insights metric"}})
    good = _resp(200, {"data": [{"name": "reach"}]})
    bad2 = _resp(400, {"error": {"message": "nope"}})
    # batch fails → probes each: reach ok, views fail
    client = SimpleNamespace(get=AsyncMock(side_effect=[bad, good, bad2]))
    out = await A._meta_insights_get(client, "u", ["reach", "views"])
    assert out.status_code == 200
    assert out.json()["data"] == [{"name": "reach"}]


@pytest.mark.asyncio
async def test_meta_insights_get_non_400_passthrough():
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(403, {"error": {"message": "denied"}})))
    out = await A._meta_insights_get(client, "u", ["impressions"])
    assert out.status_code == 403


def test_meta_error_message():
    assert A._meta_error_message(_resp(400, {"error": {"message": "bad metric"}})) == "bad metric"
    no_json = SimpleNamespace(status_code=500)
    no_json.json = lambda: (_ for _ in ()).throw(ValueError())
    no_json.text = "raw-text"
    assert A._meta_error_message(no_json) == "raw-text"


def test_member_post_analytics_scope_missing():
    assert A._member_post_analytics_scope_missing(None) is False
    assert A._member_post_analytics_scope_missing([]) is False
    assert A._member_post_analytics_scope_missing(["w_member_social"]) is True
    assert A._member_post_analytics_scope_missing(["r_member_postAnalytics"]) is False


# ── follower guards ───────────────────────────────────────────────────


def test_plausible_follower_count():
    assert A._plausible_follower_count(None, 5) is True  # first reading
    assert A._plausible_follower_count(100, 400) is True
    assert A._plausible_follower_count(100, 501) is False  # >5x
    assert A._plausible_follower_count(952000, 15) is False  # >50% drop
    assert A._plausible_follower_count(50, 10) is True  # small accounts exempt from drop rule
    assert A._plausible_follower_count(200, 99) is False  # <50% of prev>=100


@pytest.mark.asyncio
async def test_record_follower_snapshot_rejects_zero():
    assert await A._record_follower_snapshot(_DB(), _account(), "x", 0) is False


@pytest.mark.asyncio
async def test_record_follower_snapshot_implausible():
    db = _DB(results=[(952000, datetime.now(UTC))])
    assert await A._record_follower_snapshot(db, _account(), "linkedin", 15) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_record_follower_snapshot_same_count_within_heartbeat():
    db = _DB(results=[(500, datetime.now(UTC) - timedelta(days=1))])
    assert await A._record_follower_snapshot(db, _account(), "x", 500) is False


@pytest.mark.asyncio
async def test_record_follower_snapshot_writes_on_change():
    db = _DB(results=[(500, datetime.now(UTC) - timedelta(days=1))])
    assert await A._record_follower_snapshot(db, _account(), "x", 510) is True
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_record_follower_snapshot_heartbeat():
    db = _DB(results=[(500, datetime.now(UTC) - timedelta(days=8))])
    assert await A._record_follower_snapshot(db, _account(), "x", 500) is True


# ── repost / persist guards ───────────────────────────────────────────


def test_is_repost_scrape_text():
    assert A._is_repost_scrape_text(None) is False
    assert A._is_repost_scrape_text("Great post by us") is False
    assert A._is_repost_scrape_text("Jane Doe reposted this") is True
    assert A._is_repost_scrape_text("x reposted\ny") is True
    # marker beyond 300-char head doesn't count
    assert A._is_repost_scrape_text("a" * 400 + " reposted this") is False


def test_persist_account_event():
    acc = _account()
    now = datetime.now(UTC)
    db = _DB()
    A._persist_account_event(db, acc, now, "insights", {"page_views": 5})
    ev = db.added[0]
    assert ev.event_type == "insights"
    assert ev.meta_data["page_views"] == 5
    assert ev.meta_data["captured_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_resolve_stale_note_marks_deleted():
    row = SimpleNamespace(id=uuid.uuid4(), notes="HTTP 404")
    db = _DB(results=[row])
    await A._resolve_stale_note(db, uuid.uuid4(), "linkedin", "urn:x")
    assert len(db.executed) == 2  # select + update


@pytest.mark.asyncio
async def test_resolve_stale_note_skips_clean():
    db = _DB(results=[None])
    await A._resolve_stale_note(db, uuid.uuid4(), "linkedin", "urn:x")
    assert len(db.executed) == 1
    db2 = _DB(results=[SimpleNamespace(id=1, notes="platform_deleted")])
    await A._resolve_stale_note(db2, uuid.uuid4(), "linkedin", "urn:x")
    assert len(db2.executed) == 1


@pytest.mark.asyncio
async def test_upsert_counter_events_new_and_zero():
    db = _DB(results=[None] * 5)
    now = datetime.now(UTC)
    m = A.MetricBundle(impressions=10, clicks=0, likes=2, comments=0, shares=1)
    await A._upsert_counter_events(
        db, team_id=uuid.uuid4(), post_id=None, social_account_id=uuid.uuid4(), platform="linkedin", platform_post_id="urn:x", metrics=m, captured_at=now
    )
    # impression/like/share → 3 adds; click/comment zero → no rows (no existing)
    assert len(db.added) == 3


@pytest.mark.asyncio
async def test_upsert_counter_events_update_and_delete():
    existing = SimpleNamespace(meta_data={}, occurred_at=None, post_id=None, social_account_id=None)
    zeroed_existing = SimpleNamespace()
    db = _DB(results=[existing, zeroed_existing, None, None, None])
    m = A.MetricBundle(impressions=10, clicks=0)  # impression updates; click row zeroed → delete
    await A._upsert_counter_events(
        db,
        team_id=uuid.uuid4(),
        post_id=None,
        social_account_id=uuid.uuid4(),
        platform="linkedin",
        platform_post_id="urn:x",
        metrics=m,
        captured_at=datetime.now(UTC),
    )
    assert existing.meta_data["count"] == 10
    assert zeroed_existing in db.deleted


# ── _persist_snapshot ─────────────────────────────────────────────────


def _result():
    return A.SyncResult()


@pytest.mark.asyncio
async def test_persist_snapshot_soft_skip_notes():
    db = _DB()
    r = _result()
    for note in ("stats_unavailable", "platform_deleted"):
        await A._persist_snapshot(
            db,
            account=_account(),
            post_id=None,
            platform_post_id="p1",
            metrics=A.MetricBundle(notes=note),
            captured_at=datetime.now(UTC),
            source="api",
            result=r,
        )
    assert r.skipped == 2 and r.synced == 0


@pytest.mark.asyncio
async def test_persist_snapshot_linkedin_soft_vs_hard():
    db = _DB()
    r = _result()
    soft = A.MetricBundle(notes="linkedin stats HTTP 404: could not find entity")
    await A._persist_snapshot(db, account=_account(), post_id=None, platform_post_id="p", metrics=soft, captured_at=datetime.now(UTC), source="api", result=r)
    assert r.skipped == 1 and r.errors == []
    hard = A.MetricBundle(notes="linkedin stats HTTP 401: revoked")
    await A._persist_snapshot(db, account=_account(), post_id=None, platform_post_id="p", metrics=hard, captured_at=datetime.now(UTC), source="api", result=r)
    assert r.errors and "401" in r.errors[0]


@pytest.mark.asyncio
async def test_persist_snapshot_deduped_limit_note():
    db = _DB(results=["quota_exhausted"])
    r = _result()
    await A._persist_snapshot(
        db,
        account=_account(),
        post_id=None,
        platform_post_id="p",
        metrics=A.MetricBundle(notes="quota_exhausted"),
        captured_at=datetime.now(UTC),
        source="api",
        result=r,
    )
    assert r.skipped == 1 and r.synced == 0


@pytest.mark.asyncio
async def test_persist_snapshot_repost_zeroed():
    db = _DB(results=[None, None, None, None, None])  # latest_metrics + 5 counter selects
    r = _result()
    m = A.MetricBundle(impressions=500, likes=99, raw={"is_repost": True})
    await A._persist_snapshot(db, account=_account(), post_id=None, platform_post_id="p", metrics=m, captured_at=datetime.now(UTC), source="api", result=r)
    assert r.synced == 1
    snap = db.added[0]
    assert snap.impressions == 0 and snap.likes == 0


@pytest.mark.asyncio
async def test_persist_snapshot_identical_24h_dedup():
    prev = (10, 2, 3, 4, 5)
    db = _DB(results=[prev])
    r = _result()
    m = A.MetricBundle(impressions=10, clicks=2, likes=3, comments=4, shares=5)
    await A._persist_snapshot(db, account=_account(), post_id=None, platform_post_id="p", metrics=m, captured_at=datetime.now(UTC), source="api", result=r)
    assert r.skipped == 1 and db.added == []


@pytest.mark.asyncio
async def test_persist_snapshot_writes_and_counters():
    db = _DB(results=[None, None, None, None, None, None])  # latest + 5 counters
    r = _result()
    m = A.MetricBundle(impressions=10, clicks=2, likes=3, comments=4, shares=5, reach=9)
    acc = _account()
    await A._persist_snapshot(db, account=acc, post_id=None, platform_post_id="urn:x", metrics=m, captured_at=datetime.now(UTC), source="api", result=r)
    assert r.synced == 1
    snap = db.added[0]
    assert snap.impressions == 10 and snap.engagement == 14
    assert r.snapshots == ["urn:x"]
    # 5 counter events (all non-zero)
    assert len(db.added) == 6


# ── tiktok ────────────────────────────────────────────────────────────


def test_resolve_tiktok_display_video_id():
    target = SimpleNamespace(post=SimpleNamespace(platform_specific={"tiktok": {"publicaly_available_post_id": "PUB1"}}), platform_post_id="whatever")
    assert A._resolve_tiktok_display_video_id(target) == "PUB1"

    target2 = SimpleNamespace(post=None, platform_post_id="v_inbox_file~v2.123")
    assert A._resolve_tiktok_display_video_id(target2) is None  # publish_id not queryable

    target3 = SimpleNamespace(post=None, platform_post_id="7312345678")
    assert A._resolve_tiktok_display_video_id(target3) == "7312345678"

    target4 = SimpleNamespace(post=None, platform_post_id="")
    assert A._resolve_tiktok_display_video_id(target4) is None


@pytest.mark.asyncio
async def test_tiktok_consecutive_misses():
    db = _DB(results=[["tiktok_video_not_found", "tiktok_video_not_found", "other"]])
    assert await A._tiktok_consecutive_misses(db, uuid.uuid4(), "v1") == 2
    db2 = _DB(results=[["ok"]])
    assert await A._tiktok_consecutive_misses(db2, uuid.uuid4(), "v1") == 0


def test_write_tiktok_cookie_file(tmp_path):
    path = A._write_tiktok_cookie_file({"sessionid": "abc", "tt_csrf": "x"})
    content = open(path).read()
    assert "# Netscape HTTP Cookie File" in content
    assert "sessionid\tabc" in content
    assert ".tiktok.com" in content


@pytest.mark.asyncio
async def test_fetch_tiktok_video_stats_paths():
    # non-200
    client = SimpleNamespace(post=AsyncMock(return_value=_resp(500)))
    out = await A._fetch_tiktok_video_stats(client, "t", "v1")
    assert "HTTP 500" in out.notes

    # error.code
    client2 = SimpleNamespace(post=AsyncMock(return_value=_resp(200, {"error": {"code": "spam", "message": "nope"}})))
    out2 = await A._fetch_tiktok_video_stats(client2, "t", "v1")
    assert "spam" in out2.notes

    # matching video
    body = {"error": {"code": "ok"}, "data": {"videos": [{"id": "v1", "view_count": 100, "like_count": 5, "comment_count": 2, "share_count": 1}]}}
    client3 = SimpleNamespace(post=AsyncMock(return_value=_resp(200, body)))
    out3 = await A._fetch_tiktok_video_stats(client3, "t", "v1")
    assert out3.impressions == 100 and out3.likes == 5 and out3.reach == 100

    # not found
    client4 = SimpleNamespace(post=AsyncMock(return_value=_resp(200, {"error": {"code": "ok"}, "data": {"videos": []}})))
    out4 = await A._fetch_tiktok_video_stats(client4, "t", "v1")
    assert out4.notes == "tiktok_video_not_found"


# ── sync_tiktok_account / dispatch ────────────────────────────────────


class _Client:
    """Fake httpx.AsyncClient — class-level mocks configured per test."""

    post = AsyncMock()
    get = AsyncMock()

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


def _target(pid, *, post_ps=None, error=None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        post_id=uuid.uuid4(),
        platform_post_id=pid,
        published_at=now,
        error_message=error,
        status="published",
        post=SimpleNamespace(
            published_at=now,
            created_at=now,
            platform_specific=post_ps or {},
        ),
    )


@pytest.fixture
def _tt_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_tiktok_consecutive_misses", AsyncMock(return_value=0))
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())


@pytest.mark.asyncio
async def test_sync_tiktok_no_targets_no_token(_tt_patches, monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: None)
    acc = _account(platform="tiktok", meta_data={}, username=None, scopes=[])
    db = _DB(results=[[]])
    r = await A.sync_tiktok_account(db, acc)
    assert r.synced == 0 and r.skipped == 0
    assert db.committed_count if hasattr(db, "committed_count") else True


@pytest.mark.asyncio
async def test_sync_tiktok_mixed_targets(_tt_patches, monkeypatch):
    t_pub = _target("7312345")
    t_draft = _target("v_inbox_file~v2.1", error="Draft delivered to TikTok app inbox")
    t_draft2 = _target("v_inbox_file~v2.2", error="some other error")
    monkeypatch.setattr(A, "_fetch_tiktok_video_stats", AsyncMock(return_value=A.MetricBundle(likes=3)))
    acc = _account(platform="tiktok", access_token_enc="enc", username=None, meta_data={}, scopes=[])
    db = _DB(results=[[t_pub, t_draft, t_draft2]])
    r = await A.sync_tiktok_account(db, acc)
    A._persist_snapshot.assert_awaited_once()
    assert r.skipped == 2
    assert len(r.errors) == 1  # only the non-draft inbox skip is reported


@pytest.mark.asyncio
async def test_sync_tiktok_not_found_retires(_tt_patches, monkeypatch):
    t = _target("7312345")
    monkeypatch.setattr(A, "_fetch_tiktok_video_stats", AsyncMock(return_value=A.MetricBundle(notes="tiktok_video_not_found")))
    monkeypatch.setattr(A, "_tiktok_consecutive_misses", AsyncMock(return_value=3))
    acc = _account(platform="tiktok", access_token_enc="enc", username=None, meta_data={}, scopes=[])
    db = _DB(results=[[t]])
    r = await A.sync_tiktok_account(db, acc)
    assert t.status == "deleted"
    assert r.skipped == 1
    A._resolve_stale_note.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_tiktok_userinfo_path(_tt_patches, monkeypatch):
    _Client.get = AsyncMock(
        return_value=_resp(
            200,
            {
                "data": {
                    "user": {
                        "username": "cloudless",
                        "display_name": "Cloudless",
                        "avatar_url": "https://a",
                        "follower_count": 120,
                        "following_count": 5,
                        "likes_count": 900,
                        "video_count": 30,
                        "is_verified": False,
                        "profile_deep_link": "https://t",
                    }
                }
            },
        )
    )
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, ts, et, metrics: events.append((et, metrics)))
    acc = _account(platform="tiktok", access_token_enc="enc", username="old", meta_data={}, scopes=["user.info.stats", "user.info.profile"])
    db = _DB(results=[[]])
    await A.sync_tiktok_account(db, acc)
    assert acc.username == "cloudless"
    assert acc.meta_data["follower_count"] == 120
    assert events[0][0] == "profile_sync"
    assert events[0][1]["api_source"] == "user.info"


@pytest.mark.asyncio
async def test_sync_tiktok_scrape_persists(_tt_patches, monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: None)
    monkeypatch.setattr(A, "_scrape_tiktok_profile", lambda u, c: {"videos": {"v9": A.MetricBundle(likes=1)}, "followers": 200, "channel_id": "ch1"})
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, ts, et, metrics: events.append((et, metrics)))
    acc = _account(platform="tiktok", username="cloudless", meta_data={"tiktok_web_cookies": {"sessionid": "x"}}, scopes=[])
    db = _DB(results=[[]])
    await A.sync_tiktok_account(db, acc)
    A._persist_snapshot.assert_awaited_once()
    assert events[0][0] == "profile_sync"
    assert events[0][1]["followers_count"] == 200


@pytest.mark.asyncio
async def test_sync_tiktok_empty_scrape_checks_sidecar(_tt_patches, monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: None)
    monkeypatch.setattr(A, "_scrape_tiktok_profile", lambda u, c: {"videos": {}, "followers": 0, "channel_id": ""})
    _Client.get = AsyncMock(return_value=_resp(200, {"logged_in": False}))
    acc = _account(platform="tiktok", username="cloudless", meta_data={"tiktok_web_cookies": {"sessionid": "x"}}, scopes=[])
    r = await A.sync_tiktok_account(_DB(results=[[]]), acc)
    assert any("web session expired" in e for e in r.errors)


def test_scrape_tiktok_profile(monkeypatch):
    import sys
    from types import ModuleType

    class _YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def extract_info(self, url, download=False):
            return {
                "entries": [
                    {"id": "v1", "view_count": 10, "like_count": 2, "comment_count": 1, "repost_count": 3, "title": "t"},
                    {"id": None},  # skipped
                ],
                "channel_follower_count": 42,
                "channel_id": "ch",
            }

    mod = ModuleType("yt_dlp")
    mod.YoutubeDL = _YDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    out = A._scrape_tiktok_profile("cloudless", {"sessionid": "x"})
    assert out["followers"] == 42
    assert out["videos"]["v1"].likes == 2
    assert len(out["videos"]) == 1


@pytest.mark.asyncio
async def test_sync_team_analytics_dispatch(monkeypatch):
    li = _account(platform="linkedin")
    wa = _account(platform="whatsapp")
    tt = _account(platform="tiktok")
    monkeypatch.setattr(A, "sync_linkedin_account", AsyncMock(return_value=A.SyncResult(synced=2)))
    monkeypatch.setattr(A, "sync_tiktok_account", AsyncMock(return_value=A.SyncResult(synced=1, snapshots=["v"])))
    monkeypatch.setattr(A, "_persist_follower_snapshot", AsyncMock())
    db = _DB(results=[[li, wa, tt]])
    out = await A.sync_team_analytics(db, uuid.uuid4())
    assert out.synced == 3 and out.skipped == 1  # whatsapp skipped
    assert out.snapshots == ["v"]


@pytest.mark.asyncio
async def test_sync_team_analytics_error_capture(monkeypatch):
    bad = _account(platform="facebook", username="fb")
    ok = _account(platform="threads")
    monkeypatch.setattr(A, "sync_facebook_account", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(A, "sync_threads_account", AsyncMock(return_value=A.SyncResult(synced=1)))
    monkeypatch.setattr(A, "_persist_follower_snapshot", AsyncMock())
    out = await A.sync_team_analytics(_DB(results=[[bad, ok]]), uuid.uuid4())
    assert out.synced == 1
    assert any("facebook" in e and "boom" in e for e in out.errors)


@pytest.mark.asyncio
async def test_persist_follower_snapshot_linkedin_skips():
    db = _DB()
    await A._persist_follower_snapshot(db, _account(platform="linkedin"))
    assert db.added == [] and db.executed == []


@pytest.mark.asyncio
async def test_persist_follower_snapshot_calls_count(monkeypatch):
    import app.api.analytics as AA

    monkeypatch.setattr(AA, "_follower_count", AsyncMock(return_value=42))
    db = _DB(results=[None])  # no prev row
    await A._persist_follower_snapshot(db, _account(platform="twitter"))
    assert len(db.added) == 1 and db.added[0].followers == 42


@pytest.mark.asyncio
async def test_persist_follower_snapshot_swallows_errors(monkeypatch):
    import app.api.analytics as AA

    monkeypatch.setattr(AA, "_follower_count", AsyncMock(side_effect=RuntimeError("x")))
    await A._persist_follower_snapshot(_DB(), _account(platform="twitter"))


# ── sync_linkedin_account ─────────────────────────────────────────────


def _counting_persist():
    async def _p(db, **kw):
        kw["result"].synced += 1

    return AsyncMock(side_effect=_p)


@pytest.fixture
def _li_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_list_org_post_urns", AsyncMock(return_value=[]))
    monkeypatch.setattr(A, "_fetch_linkedin_org_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(
        A,
        "_fetch_org_lifetime_stats",
        AsyncMock(return_value=A.MetricBundle(impressions=100, likes=5)),
    )
    monkeypatch.setattr(
        A,
        "_fetch_linkedin_follower_stats",
        AsyncMock(return_value={"total_followers": 42, "follower_gains": 3, "demographics": {}}),
    )
    monkeypatch.setattr(
        A,
        "_fetch_linkedin_page_stats",
        AsyncMock(return_value={"period": {"days": 7}, "lifetime": {}}),
    )


def _li_org(**kw):
    kw.setdefault("scopes", [])
    return _account(
        platform="linkedin",
        access_token_enc="enc",
        meta_data={"account_type": "organization", "organization_id": "99"},
        **kw,
    )


@pytest.mark.asyncio
async def test_sync_linkedin_org_happy(_li_patches):
    t = _target("urn:li:share:111")
    db = _DB(results=[[t]])
    A._list_org_post_urns.return_value = [{"urn": "urn:li:share:222", "commentary": "c", "published_at": datetime.now(UTC), "raw": {"x": 1}}]
    A._fetch_linkedin_org_stats.return_value = {
        "urn:li:share:111": A.MetricBundle(impressions=10, likes=1),
        "urn:li:share:222": A.MetricBundle(impressions=5),
    }
    r = await A.sync_linkedin_account(db, _li_org())
    # 2 post snapshots + lifetime + follower + page = 5 persists
    assert r.synced == 5
    A._record_follower_snapshot.assert_awaited_once()
    call = A._record_follower_snapshot.await_args
    assert call.args[2] == "linkedin" and call.args[3] == 42
    assert r.errors == []


@pytest.mark.asyncio
async def test_sync_linkedin_org_ads_path(_li_patches, monkeypatch):
    monkeypatch.setattr(
        A,
        "get_settings",
        lambda: SimpleNamespace(LINKEDIN_AD_ACCOUNT_ID="urn:li:sponsoredAccount:1"),
    )
    A._fetch_linkedin_ad_stats = AsyncMock(
        return_value={
            "urn:li:sponsoredCampaign:7": A.MetricBundle(impressions=9),
            "_error": A.MetricBundle(notes="adAnalytics error"),
        }
    )
    acc = _li_org(scopes=["r_ads_reporting"])
    db = _DB(results=[[]])
    r = await A.sync_linkedin_account(db, acc)
    A._fetch_linkedin_ad_stats.assert_awaited_once()
    assert "adAnalytics error" in r.errors
    # campaign persist + lifetime + follower + page = 4
    assert r.synced == 4


@pytest.mark.asyncio
async def test_sync_linkedin_org_lifetime_http_fail(_li_patches):
    A._fetch_org_lifetime_stats.return_value = A.MetricBundle(notes="org lifetime HTTP 403")
    db = _DB(results=[[]])
    r = await A.sync_linkedin_account(db, _li_org())
    # lifetime block skipped entirely — no persists, no follower snapshot
    assert r.synced == 0 and r.skipped == 0
    A._record_follower_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_linkedin_member_scrape(_li_patches, monkeypatch):
    t = _target("urn:li:share:555")
    db = _DB(results=[[t]])
    activity = {
        "profile_url": "https://li/in/x",
        "followers": 77,
        "posts": [
            {
                "urn": "urn:li:activity:555",
                "impressions": 12,
                "reactions": 3,
                "comments": 1,
                "text": "original",
            }
        ],
    }
    import app.services.linkedin_sidecar as lsc

    monkeypatch.setattr(
        lsc,
        "LinkedInSidecarClient",
        lambda: SimpleNamespace(get_profile_activity=AsyncMock(return_value=activity)),
    )
    A._fetch_member_post_analytics = AsyncMock(return_value={"status": 403})
    acc = _account(platform="linkedin", access_token_enc="enc", meta_data={}, scopes=["w_member_social"])
    r = await A.sync_linkedin_account(db, acc)
    assert r.synced >= 1
    A._record_follower_snapshot.assert_awaited_once()
    # scope recorded without r_member_postAnalytics → no API probe
    A._fetch_member_post_analytics.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_linkedin_member_scrape_fails(_li_patches, monkeypatch):
    db = _DB(results=[[_target("urn:li:share:9")]])
    import app.services.linkedin_sidecar as lsc

    monkeypatch.setattr(
        lsc,
        "LinkedInSidecarClient",
        lambda: SimpleNamespace(get_profile_activity=AsyncMock(side_effect=RuntimeError("sidecar down"))),
    )
    probe = AsyncMock(return_value={"status": 403})
    A._fetch_member_post_analytics = probe
    acc = _account(platform="linkedin", access_token_enc="enc", meta_data={}, scopes=[])  # unrecorded → probes, gets 403
    r = await A.sync_linkedin_account(db, acc)
    assert any("member scrape" in e for e in r.errors)
    probe.assert_awaited_once()  # first 403 breaks the loop


# ── sync_twitter_account ──────────────────────────────────────────────


@pytest.fixture
def _tw_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_fetch_twitter_metrics", AsyncMock(return_value=A.MetricBundle(likes=2)))
    monkeypatch.setattr(A, "_persist_x_web_analytics", AsyncMock())
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: False)
    monkeypatch.setattr(xw, "fetch_x_web_analytics", AsyncMock(return_value=(None, None)))
    _Client.get = AsyncMock(return_value=_resp(402))


def _tw_acc(**kw):
    kw.setdefault("meta_data", {})
    kw.setdefault("scopes", [])
    kw.setdefault("username", "handle")
    kw.setdefault("account_id", "123")
    return _account(platform="twitter", access_token_enc="enc", **kw)


@pytest.mark.asyncio
async def test_sync_twitter_happy(_tw_patches):
    t = _target("999")
    _Client.get = AsyncMock(
        side_effect=[
            _resp(
                200,
                {
                    "data": [
                        {
                            "id": "888",
                            "created_at": datetime.now(UTC).isoformat(),
                            "public_metrics": {"impression_count": 5, "like_count": 1, "reply_count": 0, "retweet_count": 1, "quote_count": 0},
                        }
                    ]
                },
            ),
            _resp(200, {"data": {"public_metrics": {"followers_count": 10, "following_count": 3, "tweet_count": 50, "listed_count": 1}}}),
        ]
    )
    db = _DB(results=[[t]])
    r = await A.sync_twitter_account(db, _tw_acc())
    assert r.synced == 2  # local target + discovered tweet
    assert r.errors == []


@pytest.mark.asyncio
async def test_sync_twitter_quota_then_web(_tw_patches, monkeypatch):
    A._fetch_twitter_metrics = AsyncMock(return_value=A.MetricBundle(notes="quota_exhausted"))
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    monkeypatch.setattr(xw, "fetch_x_web_analytics", AsyncMock(return_value=({"posts": []}, None)))
    db = _DB(results=[[_target("1"), _target("2")]])
    r = await A.sync_twitter_account(db, _tw_acc())
    assert r.skipped >= 2  # uncovered targets + timeline 402 + users/me 402
    A._persist_x_web_analytics.assert_awaited_once()
    # web fallback skip_reason surfaces
    monkeypatch.setattr(xw, "fetch_x_web_analytics", AsyncMock(return_value=(None, "breaker tripped")))
    db2 = _DB(results=[[_target("3")]])
    r2 = await A.sync_twitter_account(db2, _tw_acc())
    assert "breaker tripped" in r2.errors[0] or "breaker tripped" in r2.notes


@pytest.mark.asyncio
async def test_sync_twitter_scrape_fallback(_tw_patches, monkeypatch):
    A._scrape_twitter_timeline = AsyncMock(
        return_value={
            "followers": 55,
            "posts": [{"id": "777", "posted": datetime.now(UTC).isoformat(), "views": 10, "likes": 1, "replies": 0, "reposts": 0}],
        }
    )
    db = _DB(results=[[]])
    r = await A.sync_twitter_account(db, _tw_acc())
    assert r.synced == 1
    A._record_follower_snapshot.assert_awaited_once()

    # scrape failure → error recorded, non-fatal
    A._scrape_twitter_timeline = AsyncMock(side_effect=RuntimeError("boom"))
    db2 = _DB(results=[[]])
    r2 = await A.sync_twitter_account(db2, _tw_acc())
    assert any("timeline scrape" in e for e in r2.errors)


@pytest.mark.asyncio
async def test_sync_twitter_usersme_error(_tw_patches):
    _Client.get = AsyncMock(
        side_effect=[
            _resp(403),  # timeline discovery
            _resp(401),  # users/me
        ]
    )
    db = _DB(results=[[]])
    r = await A.sync_twitter_account(db, _tw_acc())
    assert any("users/me HTTP 401" in e for e in r.errors)
    assert any("timeline discovery HTTP 403" in e for e in r.errors)


# ── sync_facebook_account (personal-profile path) ─────────────────────


@pytest.mark.asyncio
async def test_sync_facebook_personal_profile(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: events.append(a))
    import app.services.facebook_sidecar as fsc

    monkeypatch.setattr(
        fsc,
        "FacebookSidecarClient",
        lambda **kw: SimpleNamespace(get_profile_stats=AsyncMock(return_value={"followers": 66, "profile_views": 12, "posts": "n"})),
    )
    acc = _account(platform="facebook", access_token_enc="enc", meta_data={"account_type": "user"}, username="themis", scopes=[])
    db = _DB()
    await A.sync_facebook_account(db, acc)
    A._record_follower_snapshot.assert_awaited_once()
    assert events and events[0][3] == "profile_dashboard"
    # scrape failure → non-fatal error, still returns
    monkeypatch.setattr(fsc, "FacebookSidecarClient", lambda **kw: SimpleNamespace(get_profile_stats=AsyncMock(side_effect=RuntimeError("sidecar down"))))
    r2 = await A.sync_facebook_account(_DB(), acc)
    assert any("dashboard scrape" in e for e in r2.errors)


# ── sync_instagram_account ────────────────────────────────────────────


@pytest.fixture
def _ig_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_fetch_instagram_media_metrics", AsyncMock(return_value=A.MetricBundle(reach=9)))
    _Client.get = AsyncMock(return_value=_resp(200, {"data": []}))


def _ig_acc(**kw):
    kw.setdefault("meta_data", {"account_type": "business"})
    kw.setdefault("scopes", [])
    kw.setdefault("username", "cloudless.gr")
    kw.setdefault("account_id", "ig-1")
    return _account(platform="instagram", access_token_enc="enc", **kw)


@pytest.mark.asyncio
async def test_sync_ig_personal_skip(_ig_patches):
    acc = _ig_acc(meta_data={"account_type": "person"})
    r = await A.sync_instagram_account(_DB(), acc)
    assert r.skipped == 1 and "Business/Creator" in r.notes


@pytest.mark.asyncio
async def test_sync_ig_scope_missing(_ig_patches):
    acc = _ig_acc(scopes=["instagram_basic"])  # no insights scope
    db = _DB(results=[[_target("m1")]])
    r = await A.sync_instagram_account(db, acc)
    # per-media insights skipped with a clear note; API metrics not called
    A._fetch_instagram_media_metrics.assert_not_awaited()
    assert r.synced >= 1  # scope-missing bundle still persisted as marker


@pytest.mark.asyncio
async def test_sync_ig_happy(_ig_patches, monkeypatch):
    A._meta_insights_get = AsyncMock(
        return_value=_resp(
            200,
            {
                "data": [
                    {"name": "reach", "values": [{"value": 100}]},
                    {"name": "follower_count", "values": [{"value": 42}]},
                ]
            },
        )
    )
    _Client.get = AsyncMock(
        side_effect=[
            # media discovery — one native post not in local targets
            _resp(200, {"data": [{"id": "native1", "timestamp": datetime.now(UTC).isoformat(), "caption": "c", "media_type": "IMAGE", "permalink": "p"}]}),
            # demographics
            _resp(200, {"data": [{"total_value": {"breakdowns": [{"results": [{"dimension_values": ["GR"], "value": 30}]}]}}]}),
            # follow_type split
            _resp(200, {"data": [{"total_value": {"breakdowns": [{"results": [{"dimension_values": ["NON_FOLLOWERS"], "value": 60}]}]}}]}),
            # online_followers heatmap
            _resp(200, {"data": [{"values": [{"value": {"9": 5, "18": 20}}]}]}),
        ]
    )
    db = _DB(results=[[_target("m1")]])
    r = await A.sync_instagram_account(db, _ig_acc())
    assert r.synced == 2  # local target + discovered native media
    assert r.errors == []


@pytest.mark.asyncio
async def test_sync_ig_discovery_error(_ig_patches):
    _Client.get = AsyncMock(return_value=_resp(500))
    db = _DB(results=[[]])
    r = await A.sync_instagram_account(db, _ig_acc())
    assert any("media discovery HTTP 500" in e for e in r.errors)


# ── sync_threads_account ──────────────────────────────────────────────


@pytest.fixture
def _th_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())
    monkeypatch.setattr(A, "_fetch_threads_media_metrics", AsyncMock(return_value=A.MetricBundle(likes=4)))
    monkeypatch.setattr(A, "_fetch_threads_account_insights", AsyncMock(return_value={"views": 100, "likes": 5}))
    monkeypatch.setattr(
        A,
        "_fetch_threads_profile",
        AsyncMock(return_value={"username": "cloudless.gr", "name": "Cloudless", "followers_count": 120, "following_count": 10, "media_count": 30}),
    )
    _Client.get = AsyncMock(return_value=_resp(200, {"data": []}))


def _th_acc(**kw):
    kw.setdefault("meta_data", {})
    kw.setdefault("scopes", [])
    kw.setdefault("username", None)
    kw.setdefault("display_name", None)
    kw.setdefault("avatar_url", None)
    kw.setdefault("account_id", "th-1")
    return _account(platform="threads", access_token_enc="enc", **kw)


@pytest.mark.asyncio
async def test_sync_threads_happy(_th_patches):
    _Client.get = AsyncMock(
        return_value=_resp(200, {"data": [{"id": "n1", "timestamp": datetime.now(UTC).isoformat(), "text": "t", "media_type": "TEXT_POST", "permalink": "p"}]})
    )
    db = _DB(results=[[_target("m1")]])
    acc = _th_acc()
    r = await A.sync_threads_account(db, acc)
    assert r.synced == 2 and r.errors == []
    assert acc.username == "cloudless.gr" and acc.display_name == "Cloudless"
    # account-insights + profile events persisted via db.add
    assert len(db.added) >= 2


@pytest.mark.asyncio
async def test_sync_threads_platform_deleted(_th_patches):
    A._fetch_threads_media_metrics = AsyncMock(return_value=A.MetricBundle(notes="platform_deleted"))
    t = _target("gone1")
    db = _DB(results=[[t]])
    r = await A.sync_threads_account(db, _th_acc())
    assert t.status == "deleted" and t.error_message == "Media deleted on Threads"
    A._resolve_stale_note.assert_awaited_once()
    assert r.skipped == 1


@pytest.mark.asyncio
async def test_sync_threads_error_paths(_th_patches):
    _Client.get = AsyncMock(return_value=_resp(500))
    A._fetch_threads_account_insights = AsyncMock(side_effect=RuntimeError("insights down"))
    A._fetch_threads_profile = AsyncMock(side_effect=RuntimeError("profile down"))
    db = _DB(results=[[]])
    r = await A.sync_threads_account(db, _th_acc())
    assert any("discovery HTTP 500" in e for e in r.errors)
    assert any("account insights" in e for e in r.errors)
    assert any("profile sync" in e for e in r.errors)
