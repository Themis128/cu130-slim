"""Remaining branch arms in the Meta-family analytics sync: the adaptive
insights helper, the Facebook profile and Page paths, the Instagram audience
heatmap and the Threads profile refresh."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
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


@pytest.fixture
def _meta(monkeypatch):
    """No crypto, no real HTTP. Snapshots count as synced and account
    events are captured for assertions."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", _counting_persist())
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, ts, et, m: events.append((et, m)))
    monkeypatch.setattr(A, "_record_follower_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())
    return events


# ── _meta_unsupported_metric_names / _meta_insights_get ───────────────


def test_unsupported_names_drop_non_identifier_captures():
    """Only identifier-shaped names are kept. A multi-word capture is prose,
    not a metric, and must not get a real metric dropped."""
    msg = "This media does not support the impressions, total views metrics"
    assert A._meta_unsupported_metric_names(msg) == {"impressions"}


def _gets(*responses):
    return SimpleNamespace(get=AsyncMock(side_effect=list(responses)))


def _err(msg):
    return _resp(400, {"error": {"message": msg}})


@pytest.mark.asyncio
async def test_insights_out_of_range_metric_index_gives_up_after_bounded_retries():
    """A metric[N] index that doesn't fit the remaining list can't be dropped.
    The helper must stop after its retry budget (3 x metrics) and return the
    last error, not loop forever."""
    bad = _err("(#100) metric[5] must be one of the following values: reach")
    client = _gets(bad, bad, bad, bad)

    resp = await A._meta_insights_get(client, "u", ["reach"], params={"period": "day"})

    assert resp is bad
    assert client.get.await_count == 3
    assert all(c.kwargs["params"] == {"period": "day", "metric": "reach"} for c in client.get.await_args_list)


@pytest.mark.asyncio
async def test_insights_named_metric_not_requested_falls_through_to_probing():
    """If Meta names an unsupported metric we never asked for, nothing can be
    dropped, so the (#100) per-metric probe runs and merges what works."""
    client = _gets(
        _err("(#100) does not support the zzz metric"),
        _resp(200, {"data": [{"name": "a", "values": [{"value": 1}]}]}),
        _resp(400, {}),
    )

    resp = await A._meta_insights_get(client, "u", ["a", "b"])

    assert isinstance(resp, httpx.Response) and resp.status_code == 200
    assert resp.json() == {"data": [{"name": "a", "values": [{"value": 1}]}]}
    probed = [c.kwargs["params"]["metric"] for c in client.get.await_args_list]
    assert probed == ["a,b", "a", "b"]


# ── Facebook ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fb_profile_stats_without_followers_or_counters_record_nothing(_meta, monkeypatch):
    import app.services.facebook_sidecar as fsc

    monkeypatch.setattr(
        fsc,
        "FacebookSidecarClient",
        lambda **kw: SimpleNamespace(get_profile_stats=AsyncMock(return_value={"name": "Themis"})),
    )
    acc = _account(platform="facebook", access_token_enc="enc", username="themis", meta_data={"account_type": "user"}, scopes=[])

    r = await A.sync_facebook_account(_DB(), acc)

    A._record_follower_snapshot.assert_not_awaited()
    assert _meta == [] and r.errors == []


def _fb_page_without_token():
    return _account(
        platform="facebook", access_token_enc="enc", account_id="pg-1", account_type="page", username="cu", scopes=[], meta_data={"account_type": "page"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "accounts",
    [
        _resp(500),  # me/accounts refused
        _resp(200, {"data": [{"id": "some-other-page", "access_token": "wrong"}]}),  # no match
    ],
)
async def test_fb_page_token_lookup_miss_falls_back_to_user_token(_meta, accounts):
    """When me/accounts fails or doesn't list this Page, the user token is
    used. A different Page's token must never be borrowed."""
    _Client.get = AsyncMock(side_effect=[accounts, _resp(200, {"data": []})])

    await A.sync_facebook_account(_DB(results=[[]]), _fb_page_without_token())

    discovery = _Client.get.await_args_list[1]
    assert "pg-1/published_posts" in discovery.args[0]
    assert discovery.kwargs["params"]["access_token"] == "tok"


def _fb_page():
    return _account(
        platform="facebook",
        access_token_enc="enc",
        account_id="pg-1",
        account_type="page",
        username="cu",
        scopes=[],
        meta_data={"account_type": "page", "page_token": "page-tok"},
    )


@pytest.mark.asyncio
async def test_fb_attribution_skips_non_dict_values_and_keys_by_day(_meta, monkeypatch):
    monkeypatch.setattr(A, "_meta_insights_get", AsyncMock(return_value=_resp(200, {"data": []})))
    _Client.get = AsyncMock(
        side_effect=[
            _resp(200, {"data": []}),  # discovery
            _resp(
                200,
                {
                    "data": [
                        {
                            "values": [
                                {"value": 5, "end_time": "2026-10-09T07:00:00+0000"},  # flat int: not attribution
                                {"value": {"paid": 1, "non_paid": 2}, "end_time": "2026-10-10T07:00:00+0000"},
                            ]
                        }
                    ]
                },
            ),
        ]
    )

    r = await A.sync_facebook_account(_DB(results=[[]]), _fb_page())

    assert ("follower_attribution", {"by_day": {"2026-10-10": {"paid": 1, "organic": 2}}}) in _meta
    assert r.errors == []


@pytest.mark.asyncio
async def test_fb_attribution_http_error_is_silent_and_writes_nothing(_meta, monkeypatch):
    monkeypatch.setattr(A, "_meta_insights_get", AsyncMock(return_value=_resp(200, {"data": []})))
    _Client.get = AsyncMock(side_effect=[_resp(200, {"data": []}), _resp(500)])

    r = await A.sync_facebook_account(_DB(results=[[]]), _fb_page())

    assert [et for et, _ in _meta if et == "follower_attribution"] == []
    assert r.errors == []
    assert _Client.get.await_count == 2


# ── Instagram ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ig_heatmap_ignores_non_dict_values_and_keeps_hourly_max(_meta, monkeypatch):
    monkeypatch.setattr(A, "_fetch_instagram_media_metrics", AsyncMock(return_value=A.MetricBundle()))
    monkeypatch.setattr(A, "_meta_insights_get", AsyncMock(return_value=_resp(200, {"data": []})))
    _Client.get = AsyncMock(
        side_effect=[
            _resp(200, {"data": []}),  # media discovery
            _resp(200, {"data": []}),  # demographics
            _resp(200, {"data": []}),  # follow_type split
            _resp(
                200,
                {
                    "data": [
                        {
                            "values": [
                                {"value": 3},  # not a per-hour map
                                {"value": {"9": 5, "10": 1}},
                                {"value": {"9": 2, "10": None, "11": 7}},
                            ]
                        }
                    ]
                },
            ),
        ]
    )
    acc = _account(platform="instagram", access_token_enc="enc", account_id="ig-1", username="cu", scopes=[], meta_data={"account_type": "business"})

    await A.sync_instagram_account(_DB(results=[[]]), acc)

    heat = dict(_meta)["audience_activity"]
    assert heat["by_hour"] == {"9": 5, "10": 1, "11": 7}
    assert heat["peak_hours"] == ["11", "9", "10"]


# ── Threads ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_threads_profile_never_overwrites_existing_identity(_meta, monkeypatch):
    monkeypatch.setattr(A, "_fetch_threads_media_metrics", AsyncMock(return_value=A.MetricBundle()))
    monkeypatch.setattr(A, "_fetch_threads_account_insights", AsyncMock(return_value={}))
    monkeypatch.setattr(
        A,
        "_fetch_threads_profile",
        AsyncMock(
            return_value={
                "username": "new",
                "name": "New",
                "threads_profile_picture_url": "https://new",
                "followers_count": 9,
            }
        ),
    )
    _Client.get = AsyncMock(return_value=_resp(200, {"data": []}))
    acc = _account(
        platform="threads", access_token_enc="enc", account_id="th-1", username="mine", display_name="Mine", avatar_url="https://mine", meta_data={}, scopes=[]
    )
    db = _DB(results=[[_target("m1")]])

    await A.sync_threads_account(db, acc)

    assert (acc.username, acc.display_name, acc.avatar_url) == ("mine", "Mine", "https://mine")
    profile_events = [e for e in db.added if getattr(e, "event_type", None) == "profile_sync"]
    assert len(profile_events) == 1
    assert profile_events[0].meta_data["followers_count"] == 9
    assert profile_events[0].meta_data["username"] == "new"
