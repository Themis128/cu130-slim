"""Remaining branch arms in the LinkedIn and X analytics sync, plus the
dedupe gate in _persist_snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import analytics_sync as A
from tests.unit.test_analytics_sync_helpers import (
    _DB,
    _account,
    _Client,
    _counting_persist,
    _resp,
    _target,
)


def _persisted() -> dict[str, A.MetricBundle]:
    return {c.kwargs["platform_post_id"]: c.kwargs["metrics"] for c in A._persist_snapshot.await_args_list}


# ── LinkedIn helpers ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_org_stats_batch_maps_found_urns_and_marks_the_rest():
    """Elements without a URN are ignored. Each requested URN either gets the
    element that shares its numeric id or a no_stats_element marker. A stat
    is never attached to the wrong post."""
    body = {
        "elements": [
            {"totalShareStatistics": {"likeCount": 99}},  # no share/ugcPost urn
            {"share": "urn:li:share:222", "totalShareStatistics": {"likeCount": 4, "impressionCount": 50}},
        ]
    }
    client = SimpleNamespace(get=AsyncMock(return_value=_resp(200, body)))

    out = await A._fetch_linkedin_org_stats(client, "tok", "urn:li:organization:9", ["urn:li:share:111", "urn:li:share:222"])

    client.get.assert_awaited_once()  # one batched request
    assert (out["urn:li:share:222"].likes, out["urn:li:share:222"].impressions) == (4, 50)
    assert out["urn:li:share:111"].likes == 0
    assert out["urn:li:share:111"].raw == {"note": "no_stats_element"}


@pytest.mark.asyncio
async def test_org_post_discovery_stops_at_max_pages():
    """Paging stops at max_pages even when the API reports more posts."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    def page(i):
        return _resp(
            200,
            {
                "elements": [{"id": f"urn:li:share:{i}", "publishedAt": now_ms, "commentary": "c"}],
                "paging": {"total": 10_000},
            },
        )
    client = SimpleNamespace(get=AsyncMock(side_effect=[page(1), page(2), page(3)]))

    out = await A._list_org_post_urns(
        client,
        "tok",
        "urn:li:organization:9",
        since=datetime.now(UTC) - timedelta(days=1),
        max_pages=2,
    )

    assert [o["urn"] for o in out] == ["urn:li:share:1", "urn:li:share:2"]
    urls = [c.args[0] for c in client.get.await_args_list]
    assert len(urls) == 2 and "start=0" in urls[0] and "start=20" in urls[1]


# ── _persist_snapshot dedupe gate ─────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_note_is_persisted_when_it_differs_from_the_last_one(monkeypatch):
    """Data-gap notes are only collapsed when they repeat. A new note
    (scope missing → quota exhausted) is a state change and gets written."""
    monkeypatch.setattr(A, "_upsert_counter_events", AsyncMock())
    db = _DB(results=["member_postAnalytics_scope_missing", None])
    result = A.SyncResult()
    acc = _account(platform="twitter")

    await A._persist_snapshot(
        db,
        account=acc,
        post_id=None,
        platform_post_id="t1",
        metrics=A.MetricBundle(notes="quota_exhausted"),
        captured_at=datetime.now(UTC),
        source="twitter_api",
        result=result,
    )

    assert result.synced == 1 and result.skipped == 0
    assert [(s.platform_post_id, s.notes) for s in db.added] == [("t1", "quota_exhausted")]


# ── sync_linkedin_account ─────────────────────────────────────────────


@pytest.fixture
def _li(monkeypatch):
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_list_org_post_urns", AsyncMock(return_value=[]))
    monkeypatch.setattr(A, "_fetch_linkedin_org_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(A, "_fetch_org_lifetime_stats", AsyncMock(return_value=A.MetricBundle(impressions=1)))
    monkeypatch.setattr(A, "_fetch_linkedin_follower_stats", AsyncMock(return_value={"total_followers": 0}))
    monkeypatch.setattr(A, "_fetch_linkedin_page_stats", AsyncMock(return_value={}))


@pytest.mark.asyncio
async def test_org_sync_drops_bare_ids_and_never_records_zero_followers(_li):
    """A bare numeric id is ambiguous (share or ugcPost), so it isn't queried
    as a URN. A 0 follower total (scope gap) must not enter the follower
    series as a fake drop. The follower_stats snapshot is still kept."""
    acc = _account(platform="linkedin", access_token_enc="enc", scopes=[], meta_data={"account_type": "organization", "organization_id": "99"})
    db = _DB(results=[[_target("12345"), _target("urn:li:share:7")]])

    await A.sync_linkedin_account(db, acc)

    assert A._fetch_linkedin_org_stats.await_args.args[3] == ["urn:li:share:7"]
    A._record_follower_snapshot.assert_not_awaited()
    follower = [c.kwargs for c in A._persist_snapshot.await_args_list if c.kwargs["source"] == "linkedin_follower_stats"]
    assert len(follower) == 1 and follower[0]["metrics"].raw == {"total_followers": 0}


@pytest.mark.asyncio
async def test_member_sync_probes_each_urn_and_skips_empty_analytics(_li, monkeypatch):
    """Only numeric-suffix URNs go into the activity join map. An analytics
    response with no data (and no 401/403) moves on to the next URN without
    attaching creator_analytics."""
    import app.services.linkedin_sidecar as lsc

    activity = {"posts": [{"urn": "urn:li:activity:555", "impressions": 8}]}
    monkeypatch.setattr(
        lsc,
        "LinkedInSidecarClient",
        lambda: SimpleNamespace(get_profile_activity=AsyncMock(return_value=activity)),
    )
    probe = AsyncMock(side_effect=[{"status": 200}, {"status": 200}, {"status": 200, "data": {"views": 3}}])
    monkeypatch.setattr(A, "_fetch_member_post_analytics", probe)
    t_num, t_slug = _target("urn:li:share:555"), _target("urn:li:share:draft-abc")
    acc = _account(platform="linkedin", access_token_enc="enc", meta_data={}, scopes=[])

    await A.sync_linkedin_account(_DB(results=[[t_num, t_slug]]), acc)

    assert probe.await_count == 3  # share:555, share:draft-abc, activity:555
    persisted = {c.kwargs["platform_post_id"]: c.kwargs for c in A._persist_snapshot.await_args_list}
    assert persisted["urn:li:activity:555"]["post_id"] == t_num.post_id  # joined by numeric tail
    assert "creator_analytics" not in (persisted["urn:li:share:555"]["metrics"].raw or {})
    assert persisted["urn:li:activity:555"]["metrics"].raw["creator_analytics"] == {"views": 3}


# ── sync_twitter_account ──────────────────────────────────────────────


@pytest.fixture
def _tw(monkeypatch):
    import app.services.publishing as pub
    import app.services.x_web as xw

    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_fetch_twitter_metrics", AsyncMock(return_value=A.MetricBundle(likes=2)))
    monkeypatch.setattr(A, "_persist_x_web_analytics", AsyncMock())
    monkeypatch.setattr(A, "_scrape_twitter_timeline", AsyncMock(return_value={"posts": [], "followers": None}))
    monkeypatch.setattr(xw, "is_configured", lambda: False)
    monkeypatch.setattr(xw, "fetch_x_web_analytics", AsyncMock(return_value=(None, None)))
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(pub, "_refresh_oauth2_token", refresh)
    _Client.get = AsyncMock(return_value=_resp(402))
    return refresh


def _tw_acc(**kw):
    return _account(platform="twitter", access_token_enc="enc", username="handle", account_id="123", meta_data={}, scopes=[], **kw)


@pytest.mark.asyncio
async def test_naive_unexpired_token_is_used_without_refresh(_tw):
    acc = _tw_acc(token_expires_at=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None))

    await A.sync_twitter_account(_DB(results=[[_target("1")]]), acc)

    _tw.assert_not_awaited()
    assert A._fetch_twitter_metrics.await_args.args[1] == "tok"


@pytest.mark.asyncio
async def test_expired_token_with_failed_refresh_keeps_stored_token(_tw):
    acc = _tw_acc(token_expires_at=datetime.now(UTC) - timedelta(hours=1))

    await A.sync_twitter_account(_DB(results=[[_target("1")]]), acc)

    _tw.assert_awaited_once()
    assert A._fetch_twitter_metrics.await_args.args[1] == "tok"


@pytest.mark.asyncio
async def test_401_with_failed_refresh_persists_the_401_without_retrying(_tw, monkeypatch):
    fetch = AsyncMock(return_value=A.MetricBundle(notes="twitter stats HTTP 401"))
    monkeypatch.setattr(A, "_fetch_twitter_metrics", fetch)

    await A.sync_twitter_account(_DB(results=[[_target("1")]]), _tw_acc(token_expires_at=None))

    _tw.assert_awaited_once()
    fetch.assert_awaited_once()
    assert _persisted()["1"].notes == "twitter stats HTTP 401"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skip_reason", "note_has"),
    [
        (None, ""),
        ("x_web skipped: polled 2h ago", "x_web skipped: polled 2h ago"),
    ],
)
async def test_x_web_soft_skip_is_noted_but_not_an_error(_tw, monkeypatch, skip_reason, note_has):
    """When the free web fallback declines (no reason, or a rate-limit reason),
    the reason goes into notes. Only 'failed' or 'tripped' counts as an error."""
    import app.services.x_web as xw

    monkeypatch.setattr(A, "_fetch_twitter_metrics", AsyncMock(return_value=A.MetricBundle(notes="quota_exhausted")))
    monkeypatch.setattr(xw, "is_configured", lambda: True)
    web = AsyncMock(return_value=(None, skip_reason))
    monkeypatch.setattr(xw, "fetch_x_web_analytics", web)

    r = await A.sync_twitter_account(_DB(results=[[_target("1")]]), _tw_acc(token_expires_at=None))

    web.assert_awaited_once()
    assert web.await_args.kwargs["wanted_tweet_ids"] == ["1"]
    assert note_has in r.notes
    assert not any("x_web" in e for e in r.errors)
    A._persist_x_web_analytics.assert_not_awaited()
