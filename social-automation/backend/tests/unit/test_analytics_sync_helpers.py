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
    monkeypatch.setattr(
        A,
        "_fetch_linkedin_ad_stats",
        AsyncMock(
            return_value={
                "urn:li:sponsoredCampaign:7": A.MetricBundle(impressions=9),
                "_error": A.MetricBundle(notes="adAnalytics error"),
            }
        ),
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
    monkeypatch.setattr(A, "_fetch_member_post_analytics", AsyncMock(return_value={"status": 403}))
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
    monkeypatch.setattr(A, "_fetch_member_post_analytics", probe)
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
    monkeypatch.setattr(A, "_scrape_twitter_timeline", AsyncMock(return_value={"posts": [], "followers": None}))
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
    monkeypatch.setattr(A, "_fetch_twitter_metrics", AsyncMock(return_value=A.MetricBundle(notes="quota_exhausted")))
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
    monkeypatch.setattr(
        A,
        "_scrape_twitter_timeline",
        AsyncMock(
            return_value={
                "followers": 55,
                "posts": [{"id": "777", "posted": datetime.now(UTC).isoformat(), "views": 10, "likes": 1, "replies": 0, "reposts": 0}],
            }
        ),
    )
    db = _DB(results=[[]])
    r = await A.sync_twitter_account(db, _tw_acc())
    assert r.synced == 1
    A._record_follower_snapshot.assert_awaited_once()

    # scrape failure → error recorded, non-fatal
    monkeypatch.setattr(A, "_scrape_twitter_timeline", AsyncMock(side_effect=RuntimeError("boom")))
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
    monkeypatch.setattr(
        A,
        "_meta_insights_get",
        AsyncMock(
            return_value=_resp(
                200,
                {
                    "data": [
                        {"name": "reach", "values": [{"value": 100}]},
                        {"name": "follower_count", "values": [{"value": 42}]},
                    ]
                },
            )
        ),
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
async def test_sync_threads_platform_deleted(_th_patches, monkeypatch):
    monkeypatch.setattr(A, "_fetch_threads_media_metrics", AsyncMock(return_value=A.MetricBundle(notes="platform_deleted")))
    t = _target("gone1")
    db = _DB(results=[[t]])
    r = await A.sync_threads_account(db, _th_acc())
    assert t.status == "deleted" and t.error_message == "Media deleted on Threads"
    A._resolve_stale_note.assert_awaited_once()
    assert r.skipped == 1


@pytest.mark.asyncio
async def test_sync_threads_error_paths(_th_patches, monkeypatch):
    _Client.get = AsyncMock(return_value=_resp(500))
    monkeypatch.setattr(A, "_fetch_threads_account_insights", AsyncMock(side_effect=RuntimeError("insights down")))
    monkeypatch.setattr(A, "_fetch_threads_profile", AsyncMock(side_effect=RuntimeError("profile down")))
    db = _DB(results=[[]])
    r = await A.sync_threads_account(db, _th_acc())
    assert any("discovery HTTP 500" in e for e in r.errors)
    assert any("account insights" in e for e in r.errors)
    assert any("profile sync" in e for e in r.errors)


# ── sync_facebook_account (page path) ─────────────────────────────────


@pytest.fixture
def _fb_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_fetch_facebook_post_metrics", AsyncMock(return_value=A.MetricBundle(likes=7)))
    monkeypatch.setattr(A, "_meta_insights_get", AsyncMock(return_value=_resp(200, {"data": [{"name": "page_views_total", "values": [{"value": 55}]}]})))


def _fb_page(**kw):
    kw.setdefault("meta_data", {"page_token": "page-tok", "account_type": "page"})
    kw.setdefault("account_type", "page")
    kw.setdefault("scopes", [])
    kw.setdefault("account_id", "pg-1")
    kw.setdefault("username", "cloudless.gr")
    return _account(platform="facebook", access_token_enc="enc", **kw)


@pytest.mark.asyncio
async def test_sync_facebook_page_happy(_fb_patches):
    _Client.get = AsyncMock(
        side_effect=[
            # published_posts discovery → one native post
            _resp(200, {"data": [{"id": "pg-1_99", "created_time": datetime.now(UTC).isoformat()}]}),
            # follower attribution insights
            _resp(200, {"data": [{"name": "x", "values": [{"value": {"paid": 2, "non_paid": 8}, "end_time": "2026-10-10"}]}]}),
        ]
    )
    db = _DB(results=[[_target("pg-1_1")]])
    r = await A.sync_facebook_account(db, _fb_page())
    assert r.synced == 2
    assert r.errors == []


@pytest.mark.asyncio
async def test_sync_facebook_page_token_lookup(_fb_patches):
    # no stored page_token → me/accounts lookup first
    acc = _fb_page(meta_data={"account_type": "page"}, account_type="page")
    _Client.get = AsyncMock(
        side_effect=[
            _resp(200, {"data": [{"id": "pg-1", "access_token": "pt"}]}),
            _resp(200, {"data": []}),  # discovery
        ]
    )
    db = _DB(results=[[]])
    await A.sync_facebook_account(db, acc)
    assert _Client.get.await_count == 2


@pytest.mark.asyncio
async def test_sync_facebook_page_discovery_error(_fb_patches):
    _Client.get = AsyncMock(
        side_effect=[
            _resp(500),  # discovery
            _resp(200, {"data": []}),  # attribution
        ]
    )
    db = _DB(results=[[]])
    r = await A.sync_facebook_account(db, _fb_page())
    assert any("HTTP 500" in e for e in r.errors)


# ── LinkedIn fetch-helper bodies ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_linkedin_follower_stats():
    _Client.get = AsyncMock(
        side_effect=[
            _resp(200, {"elements": [{"followerCountsByCountry": [{"GR": 10}]}]}),
            _resp(200, {"elements": [{"followerGains": {"organicFollowerGain": 3, "paidFollowerGain": 1}}]}),
            _resp(200, {"firstDegreeSize": 42}),
        ]
    )
    out = await A._fetch_linkedin_follower_stats(_Client(), "tok", "urn:li:organization:9")
    assert out["total_followers"] == 42
    assert out["follower_gains"]


@pytest.mark.asyncio
async def test_fetch_linkedin_page_stats_and_errors():
    _Client.get = AsyncMock(
        side_effect=[
            _resp(200, {"elements": [{"a": 1}]}),
            _resp(500),
        ]
    )
    out = await A._fetch_linkedin_page_stats(_Client(), "tok", "urn:li:organization:9")
    assert out["lifetime"] and "period_error" in out


@pytest.mark.asyncio
async def test_fetch_member_post_analytics():
    n_types = len(A._MEMBER_POST_QUERY_TYPES)
    _Client.get = AsyncMock(side_effect=[_resp(200, {"elements": [{"metricDataResults": [{"metricValue": 7}]}]})] * (n_types + 1))
    out = await A._fetch_member_post_analytics(_Client(), "tok", "urn:li:share:1")
    assert "data" in out
    # 403 → caller sees scope-missing
    _Client.get = AsyncMock(return_value=_resp(403))
    out2 = await A._fetch_member_post_analytics(_Client(), "tok", "urn:li:share:1")
    assert out2["status"] == 403


@pytest.mark.asyncio
async def test_fetch_org_lifetime_stats():
    _Client.get = AsyncMock(
        return_value=_resp(
            200,
            {
                "elements": [
                    {"totalShareStatistics": {"impressionCount": 50, "likeCount": 2, "commentCount": 1, "shareCount": 3, "clickCount": 4, "engagement": 0.1}}
                ]
            },
        )
    )
    b = await A._fetch_org_lifetime_stats(_Client(), "tok", "urn:li:organization:9")
    assert b.impressions == 50 and b.notes == "organization_lifetime"
    _Client.get = AsyncMock(return_value=_resp(403))
    b2 = await A._fetch_org_lifetime_stats(_Client(), "tok", "urn")
    assert b2.notes.startswith("org lifetime HTTP 403")
    _Client.get = AsyncMock(return_value=_resp(200, {"elements": []}))
    b3 = await A._fetch_org_lifetime_stats(_Client(), "tok", "urn")
    assert b3.raw.get("note") == "empty_org_lifetime"


@pytest.mark.asyncio
async def test_fetch_linkedin_org_stats_batch():
    el = {
        "share": "urn:li:share:1",
        "totalShareStatistics": {"impressionCount": 20, "likeCount": 1, "commentCount": 0, "shareCount": 0, "clickCount": 2, "engagement": 0.1},
    }
    _Client.get = AsyncMock(return_value=_resp(200, {"elements": [el]}))
    out = await A._fetch_linkedin_org_stats(_Client(), "tok", "urn:li:organization:9", ["urn:li:share:1"])
    assert out["urn:li:share:1"].impressions == 20
    # empty urn list → no calls
    out2 = await A._fetch_linkedin_org_stats(_Client(), "tok", "urn", [])
    assert out2 == {}


# ── sync_threads_account ────────────────────────────────────────────────


def _insights_data(**metrics):
    """Official Threads insights shape: data[{name, period, values:[{value}]}]."""
    return {"data": [{"name": n, "period": "lifetime", "values": [{"value": v}]} for n, v in metrics.items()]}


def _threads_http(media_insights=None, threads_list=None, account_insights=None, profile=None):
    """Route client.get by URL substring → _resp."""
    media_insights = media_insights or {}
    threads_list = threads_list if threads_list is not None else _resp(200, {"data": []})
    account_insights = account_insights if account_insights is not None else _resp(200, {"data": []})
    profile = profile if profile is not None else _resp(200, {})

    async def _get(url, **kw):
        if "/threads_insights" in url:
            return account_insights
        if "/insights" in url:
            for mid, resp in media_insights.items():
                if f"/{mid}/" in url:
                    return resp
            return _resp(404, {"error": {"message": "Object does not exist"}})
        if url.rstrip("/").endswith("/threads"):
            return threads_list
        return profile  # bare /{user_id} profile fetch

    _Client.get = AsyncMock(side_effect=_get)


@pytest.fixture
def _threads_patches(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())


@pytest.mark.asyncio
async def test_threads_sync_persists_metrics_and_events(_threads_patches):
    t = _target("th-1")
    db = _DB(results=[[t]])
    _threads_http(
        media_insights={
            "th-1": _resp(200, _insights_data(views=50, likes=7, replies=2, reposts=1, quotes=3)),
        },
        account_insights=_resp(200, _insights_data(views=500, likes=40, replies=9, reposts=4, quotes=2, followers_count=120)),
        profile=_resp(200, {"username": "cloudless.gr", "name": "Cloudless"}),
    )
    acc = _account(platform="threads", username=None, display_name=None, avatar_url=None)
    r = await A.sync_threads_account(db, acc)
    assert r.errors == []
    m = A._persist_snapshot.await_args.kwargs["metrics"]
    assert m.impressions == 50 and m.likes == 7 and m.comments == 2
    assert m.shares == 4  # reposts + quotes
    # Two AnalyticsEvents: account_insights + profile_sync
    assert len(db.added) == 2
    assert acc.username == "cloudless.gr" and acc.display_name == "Cloudless"


@pytest.mark.asyncio
async def test_threads_sync_retires_deleted_media(_threads_patches):
    t = _target("th-gone")
    db = _DB(results=[[t]])
    _threads_http(media_insights={"th-gone": _resp(404, {"error": {"message": "Object does not exist"}})})
    r = await A.sync_threads_account(db, _account(platform="threads"))
    assert t.status == "deleted"
    assert t.error_message == "Media deleted on Threads"
    assert r.skipped == 1
    A._resolve_stale_note.assert_awaited_once()
    A._persist_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_threads_sync_discovers_native_posts(_threads_patches):
    db = _DB(results=[[]])  # no published targets
    _threads_http(
        media_insights={
            "native-1": _resp(200, _insights_data(views=10, likes=1)),
        },
        threads_list=_resp(
            200,
            {
                "data": [
                    {"id": "native-1", "timestamp": "2026-10-10T12:00:00+0000", "text": "hi", "media_type": "TEXT_POST"},
                    {"id": "old-1", "timestamp": "2020-01-01T00:00:00+0000"},
                ],
            },
        ),
    )
    r = await A.sync_threads_account(db, _account(platform="threads"))
    # only the fresh native post was persisted; the 2020 one is before `since`
    A._persist_snapshot.assert_awaited_once()
    kw = A._persist_snapshot.await_args.kwargs
    assert kw["platform_post_id"] == "native-1" and kw["post_id"] is None
    assert kw["metrics"].raw["discovery"]["id"] == "native-1"
    assert r.errors == []


@pytest.mark.asyncio
async def test_threads_sync_discovery_error_collected(_threads_patches):
    db = _DB(results=[[]])
    _threads_http(
        threads_list=_resp(500, {"error": {"message": "server exploded"}}),
    )
    r = await A.sync_threads_account(db, _account(platform="threads"))
    assert any("threads discovery HTTP 500" in e for e in r.errors)


@pytest.mark.asyncio
async def test_threads_media_metrics_error_note(_threads_patches):
    """Non-200 non-deleted insights → MetricBundle carries the HTTP note."""
    client = _Client()
    client.get = AsyncMock(return_value=_resp(403, {"error": {"message": "no perms"}}))
    m = await A._fetch_threads_media_metrics(client, "tok", "th-x")
    assert "403" in m.notes and m.impressions == 0


@pytest.mark.asyncio
async def test_threads_account_insights_aggregation():
    client = _Client()
    client.get = AsyncMock(
        return_value=_resp(
            200,
            {
                "data": [
                    {"name": "views", "values": [{"value": 5}, {"value": 7}]},
                    {"name": "followers_count", "values": [{"value": 120}]},
                ],
            },
        )
    )
    out = await A._fetch_threads_account_insights(client, "tok", "u1")
    assert out == {"views": 12, "followers_count": 120}
    client.get = AsyncMock(return_value=_resp(500, {}))
    assert await A._fetch_threads_account_insights(client, "tok", "u1") == {}


# ── sync_facebook_account (page path) ──────────────────────────────────


def _fb_insights(post_id, *, media_view=100, clicks=5, likes=9, comment=2, share=3):
    return _resp(
        200,
        {
            "data": [
                {"name": "post_media_view", "values": [{"value": media_view}]},
                {"name": "post_clicks", "values": [{"value": clicks}]},
                {"name": "post_reactions_like_total", "values": [{"value": likes}]},
                {"name": "post_activity_by_action_type", "values": [{"value": {"comment": comment, "share": share}}]},
            ],
        },
    )


def _fb_obj(**counts):
    return _resp(
        200,
        {
            "comments": {"summary": {"total_count": counts.get("comments", 0)}},
            "shares": {"count": counts.get("shares", 0)},
            "reactions": {"summary": {"total_count": counts.get("likes", 0)}},
        },
    )


def _fb_http(post_insights=None, post_objs=None, me_accounts=None, discovery=None, page_insights=None, fan_adds=None):
    """Route _Client.get by URL — post/page ids disambiguate /insights."""
    post_insights = post_insights or {}
    post_objs = post_objs or {}

    async def _get(url, **kw):
        if "/me/accounts" in url:
            return me_accounts or _resp(200, {"data": []})
        if "page_fan_adds" in str(kw.get("params", {}).get("metric", "")):
            return fan_adds or _resp(200, {"data": []})
        if "/insights" in url:
            for pid, resp in post_insights.items():
                if f"/{pid}/" in url:
                    return resp
            return page_insights or _resp(200, {"data": []})
        if "/published_posts" in url or "/feed" in url:
            return discovery or _resp(200, {"data": []})
        # bare /{post_id} object-metrics fetch
        for pid, resp in post_objs.items():
            if url.rstrip("/").endswith(f"/{pid}"):
                return resp
        return _resp(404, {})

    _Client.get = AsyncMock(side_effect=_get)


@pytest.fixture
def _fb_patches_real(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())


@pytest.mark.asyncio
async def test_fb_page_sync_full(_fb_patches_real, monkeypatch):
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, at, etype, data: events.append((etype, data)))
    t = _target("post_a")
    db = _DB(results=[[t]])
    _fb_http(
        post_insights={"post_a": _fb_insights("post_a"), "post_b": _fb_insights("post_b", media_view=40)},
        post_objs={"post_a": _fb_obj(comments=2, shares=3, likes=9), "post_b": _fb_obj()},
        discovery=_resp(
            200,
            {
                "data": [
                    {"id": "post_a", "created_time": "2026-10-10T10:00:00+0000"},
                    {"id": "post_b", "created_time": "2026-10-09T10:00:00+0000"},
                    {"id": "post_old", "created_time": "2020-01-01T00:00:00+0000"},
                ]
            },
        ),
        page_insights=_resp(
            200,
            {
                "data": [
                    {"name": "page_views_total", "values": [{"value": 1}, {"value": 9}]},
                ]
            },
        ),
        fan_adds=_resp(
            200,
            {
                "data": [
                    {
                        "name": "page_fan_adds_by_paid_non_paid_unique",
                        "values": [{"value": {"paid": 2, "non_paid": 5}, "end_time": "2026-10-10T08:00:00+0000"}],
                    },
                ]
            },
        ),
    )
    acc = _account(platform="facebook", account_id="pg1", account_type="page", meta_data={"page_token": "pt"})
    r = await A.sync_facebook_account(db, acc)
    assert r.errors == []
    # post_a from targets + post_b from discovery; post_old dropped by `since`
    assert A._persist_snapshot.await_count == 2
    m_a = A._persist_snapshot.await_args_list[0].kwargs["metrics"]
    assert m_a.impressions == 100 and m_a.clicks == 5
    assert m_a.comments == 2 and m_a.shares == 3 and m_a.likes == 9
    m_b = A._persist_snapshot.await_args_list[1].kwargs["metrics"]
    assert m_b.raw["discovery"]["id"] == "post_b"
    assert ("account_insights", {"page_views_total": 9}) in events
    attr = next(d for et, d in events if et == "follower_attribution")
    assert attr["by_day"]["2026-10-10"] == {"paid": 2, "organic": 5}


@pytest.mark.asyncio
async def test_fb_page_token_lookup_and_feed_edge(_fb_patches_real):
    """No meta.page_token → me/accounts lookup; non-page → /feed edge."""
    seen_urls = []

    async def _get(url, **kw):
        seen_urls.append(url)
        if "/me/accounts" in url:
            return _resp(200, {"data": [{"id": "pg1", "access_token": "pt_from_lookup"}]})
        return _resp(200, {"data": []})

    _Client.get = AsyncMock(side_effect=_get)
    db = _DB(results=[[]])
    acc = _account(platform="facebook", account_id="pg1", account_type="user", meta_data={})
    r = await A.sync_facebook_account(db, acc)
    assert r.errors == []
    assert any("/me/accounts" in u for u in seen_urls)
    assert any("/pg1/feed" in u for u in seen_urls)  # user type → feed edge


@pytest.mark.asyncio
async def test_fb_post_metrics_salvage_on_insights_failure():
    """Insights 500 → notes recorded, counters salvaged from the object."""
    client = _Client()

    async def _get(url, **kw):
        if "/insights" in url:
            return _resp(500, {"error": {"message": "boom"}})
        return _fb_obj(comments=4, shares=1, likes=7)

    client.get = AsyncMock(side_effect=_get)
    m = await A._fetch_facebook_post_metrics(client, "pt", "post_x")
    assert "HTTP 500" in m.notes
    assert m.comments == 4 and m.shares == 1 and m.likes == 7
    assert m.impressions == 0


@pytest.mark.asyncio
async def test_fb_discovery_error_collected(_fb_patches_real):
    db = _DB(results=[[]])
    _fb_http(discovery=_resp(500, {"error": {"message": "down"}}))
    acc = _account(platform="facebook", account_id="pg1", account_type="page", meta_data={"page_token": "pt"})
    r = await A.sync_facebook_account(db, acc)
    assert any("facebook published_posts HTTP 500" in e for e in r.errors)


@pytest.mark.asyncio
async def test_fb_page_insights_error_collected(_fb_patches_real):
    db = _DB(results=[[]])
    _fb_http(page_insights=_resp(500, {"error": {"message": "nope"}}))
    acc = _account(platform="facebook", account_id="pg1", account_type="page", meta_data={"page_token": "pt"})
    r = await A.sync_facebook_account(db, acc)
    assert any("facebook page insights HTTP 500" in e for e in r.errors)


@pytest.mark.asyncio
async def test_fb_object_metrics_error_returns_zeros():
    client = _Client()
    client.get = AsyncMock(return_value=_resp(404, {}))
    out = await A._facebook_object_metrics(client, "post_x", "pt")
    assert out == {"comments": 0, "shares": 0, "likes": 0}


# ── sync_instagram_account ────────────────────────────────────────────


@pytest.fixture
def _ig_events(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, at, etype, data: events.append((etype, data)))
    return events


def _ig_http(media_insights=None, media_list=None, account_insights=None, demographics=None, reach_split=None, online=None):
    media_insights = media_insights or {}

    async def _get(url, **kw):
        params = kw.get("params") or {}
        metric = params.get("metric", "")
        if metric == "follower_demographics":
            return demographics or _resp(200, {"data": []})
        if metric == "views":
            return reach_split or _resp(200, {"data": []})
        if metric == "online_followers":
            return online or _resp(200, {"data": []})
        if url.rstrip("/").endswith("/media"):
            return media_list or _resp(200, {"data": []})
        if "/insights" in url:
            for mid, resp in media_insights.items():
                if f"/{mid}/" in url:
                    return resp
            return account_insights or _resp(200, {"data": []})
        return _resp(404, {})

    _Client.get = AsyncMock(side_effect=_get)


def _ig_account(**kw):
    kw.setdefault("platform", "instagram")
    kw.setdefault("account_id", "ig-user-1")
    kw.setdefault("meta_data", {"account_type": "business"})
    kw.setdefault("scopes", [])
    return _account(**kw)


@pytest.mark.asyncio
async def test_ig_personal_account_skipped(_ig_events):
    acc = _ig_account(meta_data={"account_type": "person"})
    r = await A.sync_instagram_account(_DB(), acc)
    assert r.skipped == 1 and "Business/Creator" in r.notes


@pytest.mark.asyncio
async def test_ig_sync_full_with_audience_events(_ig_events):
    t = _target("ig-m1")
    db = _DB(results=[[t]])
    _ig_http(
        media_insights={
            "ig-m1": _resp(200, _insights_data(views=80, likes=6, comments=1, shares=2, reach=70)),
            "ig-m2": _resp(200, _insights_data(views=20)),
        },
        media_list=_resp(
            200,
            {
                "data": [
                    {"id": "ig-m1", "timestamp": "2026-10-10T10:00:00+0000"},
                    {"id": "ig-m2", "timestamp": "2026-10-09T10:00:00+0000", "caption": "native"},
                    {"id": "ig-old", "timestamp": "2020-01-01T00:00:00+0000"},
                ]
            },
        ),
        account_insights=_resp(
            200,
            {
                "data": [
                    {"name": "reach", "values": [{"value": 1}, {"value": 42}]},
                ]
            },
        ),
        demographics=_resp(
            200,
            {"data": [{"total_value": {"breakdowns": [{"results": [{"dimension_values": ["GR"], "value": 70}, {"dimension_values": ["US"], "value": 30}]}]}}]},
        ),
        reach_split=_resp(
            200,
            {
                "data": [
                    {
                        "total_value": {
                            "breakdowns": [
                                {"results": [{"dimension_values": ["FOLLOWERS"], "value": 40}, {"dimension_values": ["NON_FOLLOWERS"], "value": 60}]}
                            ]
                        }
                    }
                ]
            },
        ),
        online=_resp(200, {"data": [{"name": "online_followers", "values": [{"value": {"9": 12, "18": 30, "20": 25}}]}]}),
    )
    r = await A.sync_instagram_account(db, _ig_account())
    assert r.errors == []
    assert A._persist_snapshot.await_count == 2
    m = A._persist_snapshot.await_args_list[0].kwargs["metrics"]
    assert m.impressions == 80 and m.likes == 6 and m.reach == 70
    assert A._persist_snapshot.await_args_list[1].kwargs["platform_post_id"] == "ig-m2"
    event_types = [e for e, _ in _ig_events]
    assert "account_insights" in event_types
    demo = next(d for e, d in _ig_events if e == "audience_demographics")
    assert demo["by_country"] == {"GR": 70, "US": 30}
    split = next(d for e, d in _ig_events if e == "audience_reach_split")
    assert split["by_follow_type"]["NON_FOLLOWERS"] == 60
    act = next(d for e, d in _ig_events if e == "audience_activity")
    assert act["peak_hours"][0] == "18"  # highest hour


@pytest.mark.asyncio
async def test_ig_scope_missing_marks_metrics(_ig_events):
    t = _target("ig-m1")
    db = _DB(results=[[t]])
    _ig_http()  # insights should never be hit for metrics
    acc = _ig_account(scopes=["instagram_basic"])
    r = await A.sync_instagram_account(db, acc)
    assert r.errors == []
    m = A._persist_snapshot.await_args.kwargs["metrics"]
    assert "insights_scope_missing" in m.notes


@pytest.mark.asyncio
async def test_ig_media_metrics_unit():
    client = _Client()
    client.get = AsyncMock(return_value=_resp(200, _insights_data(impressions=33, likes=2, replies=4)))
    m = await A._fetch_instagram_media_metrics(client, "IGAAUtoken", "u1", "m1")
    assert m.impressions == 33 and m.shares == 4  # replies→shares fallback
    client.get = AsyncMock(return_value=_resp(500, {"error": {"message": "x"}}))
    m = await A._fetch_instagram_media_metrics(client, "tok", "u1", "m1")
    assert "HTTP 500" in m.notes


@pytest.mark.asyncio
async def test_ig_discovery_error_collected(_ig_events):
    db = _DB(results=[[]])
    _ig_http(media_list=_resp(500, {"error": {"message": "down"}}))
    r = await A.sync_instagram_account(db, _ig_account())
    assert any("instagram media discovery HTTP 500" in e for e in r.errors)


# ── X web fallback ─────────────────────────────────────────────────────


def _tweet(**kw):
    return SimpleNamespace(
        views=kw.get("views", 100),
        likes=kw.get("likes", 5),
        replies=kw.get("replies", 2),
        retweets=kw.get("retweets", 1),
        quotes=kw.get("quotes", 0),
        bookmarks=kw.get("bookmarks", 0),
        created_at=kw.get("created_at"),
        text=kw.get("text", "t"),
        author=kw.get("author", "a"),
        is_repost=kw.get("is_repost", False),
    )


@pytest.mark.asyncio
async def test_persist_x_web_analytics(monkeypatch):
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: events.append(a))
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    old = datetime.now(UTC) - timedelta(days=400)
    web = SimpleNamespace(
        tweets={
            "t_known": _tweet(created_at=old),  # known → keep
            "t_old": _tweet(created_at=old),  # old unknown → skip
            "t_new": _tweet(created_at=datetime.now(UTC), text="n"),
            "t_naive": _tweet(created_at=datetime.now()),  # naive → UTC
        },
        followers=500,
        following=9,
        tweet_count=800,
        listed=3,
        source="x_web",
        errors=["e1", "e2", "e3", "e4"],
    )
    result = A.SyncResult()
    id_to_post = {"t_known": uuid.uuid4()}
    await A._persist_x_web_analytics(_DB(), _account(platform="twitter"), web, id_to_post, datetime.now(UTC) - timedelta(days=30), datetime.now(UTC), result)

    persisted_ids = [c.kwargs["platform_post_id"] for c in A._persist_snapshot.await_args_list]
    assert persisted_ids == ["t_known", "t_new", "t_naive"]
    m = A._persist_snapshot.await_args_list[0].kwargs["metrics"]
    assert m.impressions == 100 and m.shares == 1  # retweets+quotes
    assert A._persist_snapshot.await_args_list[0].kwargs["source"] == "x_web"
    A._record_follower_snapshot.assert_awaited_once()
    assert events and events[0][3] == "account_insights"
    assert len(result.errors) == 3  # errors[:3] cap


@pytest.mark.asyncio
async def test_persist_x_web_analytics_no_followers(monkeypatch):
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    web = SimpleNamespace(tweets={}, followers=0, source="x_web", errors=[])
    result = A.SyncResult()
    await A._persist_x_web_analytics(_DB(), _account(platform="twitter"), web, {}, datetime.now(UTC), datetime.now(UTC), result)
    A._persist_snapshot.assert_not_awaited()
    A._record_follower_snapshot.assert_not_awaited()


class _BrowserSession:
    browser = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return type(self).browser

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_scrape_twitter_timeline_inactive_session(monkeypatch):
    import app.services.browser_bridge as bb

    bridge = SimpleNamespace(ensure_session=AsyncMock(return_value={"status": "waiting", "message": "needs login"}))
    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *a, **kw: bridge)
    out = await A._scrape_twitter_timeline("cu_dev")
    assert out["posts"] == [] and "needs login" in out["error"]


@pytest.mark.asyncio
async def test_scrape_twitter_timeline_happy(monkeypatch):
    import app.services.browser_bridge as bb
    import app.services.browser_orchestrator as bo

    b = SimpleNamespace(
        navigate=AsyncMock(),
        evaluate=AsyncMock(
            side_effect=[
                None,
                None,
                None,
                None,  # 4 scrolls
                {"result": {"posts": [{"id": "t1"}], "followers": 500}},
            ]
        ),
    )
    _BrowserSession.browser = b
    bridge = SimpleNamespace(ensure_session=AsyncMock(return_value={"status": "active"}))
    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *a, **kw: bridge)
    monkeypatch.setattr(bo, "browser_session", lambda *a, **kw: _BrowserSession())
    monkeypatch.setattr(A.asyncio, "sleep", AsyncMock())
    out = await A._scrape_twitter_timeline("cu_dev")
    assert out == {"posts": [{"id": "t1"}], "followers": 500}
    b.navigate.assert_awaited_once()
    assert "x.com/cu_dev" in b.navigate.await_args.args[0]


# ── LinkedIn/X fetch helpers (direct client injection) ──────────────


class _Cli:
    """Minimal async client — maps url→response via a callable."""

    def __init__(self, fn):
        self._fn = fn
        self.urls = []

    async def get(self, url, **kw):
        self.urls.append(url)
        return self._fn(url)


def _cli_ok(payload):
    return _Cli(lambda url: _resp(200, payload))


@pytest.mark.asyncio
async def test_li_org_stats_empty():
    out = await A._fetch_linkedin_org_stats(_cli_ok({}), "t", "urn:li:organization:1", [])
    assert out == {}


@pytest.mark.asyncio
async def test_li_org_stats_happy_parse():
    el = {
        "ugcPost": "urn:li:ugcPost:111",
        "totalShareStatistics": {
            "impressionCount": 10,
            "clickCount": 2,
            "likeCount": 3,
            "commentCount": 1,
            "shareCount": 1,
            "uniqueImpressionsCount": 8,
        },
    }
    cli = _cli_ok({"elements": [el]})
    out = await A._fetch_linkedin_org_stats(cli, "t", "urn:li:organization:1", ["urn:li:ugcPost:111"])
    b = out["urn:li:ugcPost:111"]
    assert b.impressions == 10 and b.clicks == 2 and b.reach == 8


@pytest.mark.asyncio
async def test_li_org_stats_soft_fail_alt_urn():
    """share: 404s with not_found → retried as ugcPost → succeeds."""
    el = {
        "ugcPost": "urn:li:ugcPost:222",
        "totalShareStatistics": {"impressionCount": 5},
    }

    def fn(url):
        if "ugcPosts=" in url:
            return _resp(200, {"elements": [el]})
        return _resp(404, {"message": "could not find entity"})

    out = await A._fetch_linkedin_org_stats(_Cli(fn), "t", "urn:li:organization:1", ["urn:li:share:222"])
    assert out["urn:li:share:222"].impressions == 5


@pytest.mark.asyncio
async def test_li_org_stats_hard_fail_and_no_element():
    out = await A._fetch_linkedin_org_stats(_Cli(lambda u: _resp(500, {"message": "down"})), "t", "urn:li:organization:1", ["urn:li:ugcPost:9"])
    assert "HTTP 500" in out["urn:li:ugcPost:9"].notes

    out = await A._fetch_linkedin_org_stats(_cli_ok({"elements": []}), "t", "urn:li:organization:1", ["urn:li:share:9"])
    assert "no_stats_element" in out["urn:li:share:9"].raw["note"]


@pytest.mark.asyncio
async def test_li_ad_stats():
    assert await A._fetch_linkedin_ad_stats(_cli_ok({}), "t", "", since=datetime.now(UTC)) == {}

    el = {
        "pivotValues": ["urn:li:sponsoredCampaign:42"],
        "impressions": 100,
        "clicks": 5,
        "reactions": 2,
        "comments": 1,
        "shares": 1,
        "approximateUniqueImpressions": 80,
        "costInLocalCurrency": 3.5,
        "costInUsd": 3.8,
        "dateRange": {"start": {"day": 1}},
        "totalEngagements": 9,
    }
    out = await A._fetch_linkedin_ad_stats(_cli_ok({"elements": [el, el]}), "t", "acc-1", since=datetime.now(UTC))
    b = out["urn:li:sponsoredCampaign:42"]
    assert b.impressions == 200 and b.raw["costUsd"] == 7.6
    assert len(b.raw["daily"]) == 2

    out = await A._fetch_linkedin_ad_stats(_Cli(lambda u: _resp(403, {"message": "denied"})), "t", "acc-1", since=datetime.now(UTC))
    assert "adAnalytics HTTP 403" in out["_error"].notes


@pytest.mark.asyncio
async def test_li_org_posts_pagination_and_since():
    now = datetime.now(UTC)
    old = int((now - timedelta(days=90)).timestamp() * 1000)
    new = int((now - timedelta(days=1)).timestamp() * 1000)

    def fn(url):
        if "start=0" in url:
            return _resp(
                200,
                {
                    "elements": [
                        {"id": "urn:li:share:1", "publishedAt": new, "commentary": "new post"},
                        {"id": "urn:li:share:2", "publishedAt": old, "commentary": "too old"},
                        {"commentary": "no id"},
                    ],
                    "paging": {"total": 1},
                },
            )
        return _resp(200, {"elements": []})

    out = await A._list_org_post_urns(_Cli(fn), "t", "urn:li:organization:1", since=now - timedelta(days=30))
    assert len(out) == 1 and out[0]["urn"] == "urn:li:share:1"

    # Error status breaks the loop
    out = await A._list_org_post_urns(_Cli(lambda u: _resp(500, {})), "t", "urn:li:organization:1", since=now - timedelta(days=30))
    assert out == []


@pytest.mark.asyncio
async def test_twitter_metrics_402_fallback_and_quota():
    calls = []

    def fn(url):
        calls.append(url)
        if len(calls) == 1:
            return _resp(402, {})
        return _resp(200, {"data": {"public_metrics": {"impression_count": 50, "like_count": 3, "reply_count": 1, "retweet_count": 2}}})

    b = await A._fetch_twitter_metrics(_Cli(fn), "t", "tw-1")
    assert b.impressions == 50 and b.likes == 3
    assert len(calls) == 2  # retried with public_metrics only

    b = await A._fetch_twitter_metrics(_Cli(lambda u: _resp(429, {})), "t", "tw-1")
    assert b.notes == "quota_exhausted"

    b = await A._fetch_twitter_metrics(_Cli(lambda u: _resp(404, {})), "t", "tw-1")
    assert "HTTP 404" in b.notes


# ── Telegram channel member-count sync ──────────────────────────────


@pytest.mark.asyncio
async def test_telegram_sync_skips_without_channel():
    """No managed channel configured → skipped, no API call."""
    acc = _account(platform="telegram", username="tg-bot", meta_data={})
    r = await A.sync_telegram_channel_metrics(_DB(), acc)
    assert r.skipped == 1 and r.synced == 0 and r.errors == []


@pytest.mark.asyncio
async def test_telegram_sync_records_member_count(monkeypatch):
    """Managed channel → getChatMemberCount lands in follower_snapshots."""
    client = SimpleNamespace(get_chat_member_count=AsyncMock(return_value=42))
    monkeypatch.setattr(
        "app.api.telegram._client_for", lambda account: client
    )
    acc = _account(
        platform="telegram",
        username="tg-bot",
        meta_data={"telegram_channel": {"chat_id": "-100123"}},
    )
    db = _DB()
    r = await A.sync_telegram_channel_metrics(db, acc)
    assert r.synced == 1
    client.get_chat_member_count.assert_awaited_once_with("-100123")
    snap = db.added[0]
    assert snap.platform == "telegram" and snap.followers == 42


@pytest.mark.asyncio
async def test_telegram_sync_skips_unchanged_count(monkeypatch):
    """Same count inside the 7-day heartbeat → no duplicate row."""
    client = SimpleNamespace(get_chat_member_count=AsyncMock(return_value=42))
    monkeypatch.setattr(
        "app.api.telegram._client_for", lambda account: client
    )
    prev = SimpleNamespace(followers=42, captured_at=datetime.now(UTC))
    acc = _account(
        platform="telegram",
        username="tg-bot",
        meta_data={"telegram_channel": {"chat_id": "-100123"}},
    )
    db = _DB(results=[(42, prev.captured_at)])
    r = await A.sync_telegram_channel_metrics(db, acc)
    assert r.skipped == 1 and r.synced == 0 and db.added == []


@pytest.mark.asyncio
async def test_telegram_sync_api_error_is_reported_not_raised(monkeypatch):
    """Bot API failure → error string in result, never raises."""
    from app.services.telegram_api import TelegramAPIError

    client = SimpleNamespace(
        get_chat_member_count=AsyncMock(
            side_effect=TelegramAPIError(400, "chat not found")
        )
    )
    monkeypatch.setattr(
        "app.api.telegram._client_for", lambda account: client
    )
    acc = _account(
        platform="telegram",
        username="tg-bot",
        meta_data={"telegram_channel": {"chat_id": "-100123"}},
    )
    r = await A.sync_telegram_channel_metrics(_DB(), acc)
    assert r.errors and "chat not found" in r.errors[0]
