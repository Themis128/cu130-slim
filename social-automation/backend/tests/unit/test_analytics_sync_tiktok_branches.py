"""Remaining branch arms in the TikTok analytics sync.

The main one is the inbox self-heal. A MEDIA_UPLOAD inbox post keeps its
Content Posting publish_id, which is not a Display video id, so that post
skips every sync. The scraped profile grid is matched back to it by publish
time, and the real video id is written to platform_specific.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import analytics_sync as A
from tests.unit.test_analytics_sync_helpers import (
    _DB,
    _account,
    _Client,
    _resp,
    _target,
)


@pytest.fixture
def _tt_patches(monkeypatch):
    """Same isolation as the helpers suite: no crypto, HTTP or DB writes."""
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok" if t else None)
    monkeypatch.setattr(A, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(A, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(A, "_persist_account_event", lambda *a, **k: None)
    monkeypatch.setattr(A, "_tiktok_consecutive_misses", AsyncMock(return_value=0))
    monkeypatch.setattr(A, "_resolve_stale_note", AsyncMock())




_COOKIES = {"tiktok_web_cookies": {"sessionid": "x"}}


def _scraped(videos: dict[str, int | None]) -> dict:
    """Scraped grid: video id -> unix publish timestamp (None = missing)."""
    return {
        "videos": {
            vid: A.MetricBundle(likes=1, raw={"timestamp": ts})
            for vid, ts in videos.items()
        },
        "followers": 7,
        "channel_id": "ch",
    }


def _snapshots_by_vid() -> dict[str, uuid.UUID | None]:
    return {
        c.kwargs["platform_post_id"]: c.kwargs["post_id"]
        for c in A._persist_snapshot.await_args_list
    }


# ── inbox self-heal ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inbox_post_adopts_closest_scraped_video(_tt_patches, monkeypatch):
    now = datetime.now(UTC)
    inbox = _target("v_inbox_file~v2.1")
    inbox.published_at = now - timedelta(days=3)
    known = _target("700", post_ps={"tiktok": {"publicaly_available_post_id": "700"}})
    ts = lambda d: int((inbox.published_at + d).timestamp())  # noqa: E731
    monkeypatch.setattr(A, "decrypt_token", lambda _t: None)
    monkeypatch.setattr(
        A,
        "_scrape_tiktok_profile",
        lambda _u, _c: _scraped(
            {
                "700": ts(timedelta(0)),  # exact time, but already claimed by `known`
                "far": ts(timedelta(hours=20)),
                "near": ts(timedelta(hours=-2)),  # closest unclaimed → wins
                "old": ts(timedelta(days=15)),  # outside the 14-day window
                "nots": None,  # no timestamp → never a candidate
            }
        ),
    )
    acc = _account(platform="tiktok", username="@cu", meta_data=dict(_COOKIES), scopes=[])

    await A.sync_tiktok_account(_DB(results=[[inbox, known]]), acc)

    assert inbox.post.platform_specific == {"tiktok": {"publicaly_available_post_id": "near"}}
    snaps = _snapshots_by_vid()
    assert snaps["near"] == inbox.post_id
    assert snaps["700"] == known.post_id
    assert snaps["far"] is None and snaps["old"] is None and snaps["nots"] is None


@pytest.mark.asyncio
async def test_two_inbox_posts_never_claim_the_same_video(_tt_patches, monkeypatch):
    """Once a scraped video is adopted it is claimed. A second inbox post with
    the same publish time gets nothing rather than a duplicate mapping."""
    now = datetime.now(UTC)
    first, second = _target("v_inbox_a"), _target("v_inbox_b")
    first.published_at = second.published_at = now - timedelta(hours=1)
    monkeypatch.setattr(A, "decrypt_token", lambda _t: None)
    monkeypatch.setattr(
        A, "_scrape_tiktok_profile",
        lambda _u, _c: _scraped({"only": int(first.published_at.timestamp())}),
    )
    acc = _account(platform="tiktok", username="cu", meta_data=dict(_COOKIES), scopes=[])

    await A.sync_tiktok_account(_DB(results=[[first, second]]), acc)

    assert first.post.platform_specific["tiktok"]["publicaly_available_post_id"] == "only"
    assert second.post.platform_specific == {}
    assert _snapshots_by_vid()["only"] == first.post_id


@pytest.mark.asyncio
async def test_inbox_target_without_loaded_post(_tt_patches, monkeypatch):
    """If the Post row isn't loaded, the scraped row still joins to the
    target's post_id. There is just nowhere to write the id back."""
    pub = datetime.now(UTC) - timedelta(days=1)
    t = SimpleNamespace(
        post_id=uuid.uuid4(), platform_post_id="v_inbox_x", published_at=pub,
        error_message=None, status="published", post=None,
    )
    monkeypatch.setattr(A, "decrypt_token", lambda _t: None)
    monkeypatch.setattr(
        A, "_scrape_tiktok_profile", lambda _u, _c: _scraped({"v1": int(pub.timestamp()) + 60})
    )
    acc = _account(platform="tiktok", username="cu", meta_data=dict(_COOKIES), scopes=[])

    await A.sync_tiktok_account(_DB(results=[[t]]), acc)

    assert _snapshots_by_vid()["v1"] == t.post_id
    assert t.post is None


@pytest.mark.asyncio
async def test_inbox_post_with_no_candidate_in_window_stays_unmapped(_tt_patches, monkeypatch):
    inbox = _target("v_inbox_file~v2.9")
    monkeypatch.setattr(A, "decrypt_token", lambda _t: None)
    monkeypatch.setattr(
        A, "_scrape_tiktok_profile",
        lambda _u, _c: _scraped({"stale": int((inbox.published_at - timedelta(days=30)).timestamp())}),
    )
    acc = _account(platform="tiktok", username="cu", meta_data=dict(_COOKIES), scopes=[])

    await A.sync_tiktok_account(_DB(results=[[inbox]]), acc)

    assert inbox.post.platform_specific == {}
    assert _snapshots_by_vid() == {"stale": None}


# ── Display API / user.info arms ──────────────────────────────────────


@pytest.mark.asyncio
async def test_single_not_found_is_persisted_not_retired(_tt_patches, monkeypatch):
    """One miss could just be an API gap. It is recorded as a snapshot, and
    the target is only retired after two misses in a row."""
    t = _target("7312345")
    monkeypatch.setattr(
        A, "_fetch_tiktok_video_stats",
        AsyncMock(return_value=A.MetricBundle(notes="tiktok_video_not_found")),
    )
    monkeypatch.setattr(A, "_tiktok_consecutive_misses", AsyncMock(return_value=1))
    acc = _account(platform="tiktok", access_token_enc="enc", username=None, meta_data={}, scopes=[])

    await A.sync_tiktok_account(_DB(results=[[t]]), acc)

    assert t.status == "published"
    A._resolve_stale_note.assert_not_awaited()
    call = A._persist_snapshot.await_args
    assert call.kwargs["platform_post_id"] == "7312345"
    assert call.kwargs["metrics"].notes == "tiktok_video_not_found"


@pytest.mark.asyncio
async def test_user_info_empty_user_changes_nothing(_tt_patches, monkeypatch):
    _Client.get = AsyncMock(return_value=_resp(200, {"data": {"user": {}}}))
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda *a: events.append(a))
    acc = _account(
        platform="tiktok", access_token_enc="enc", username="keep", display_name="Keep",
        meta_data={}, scopes=["user.info.stats"],
    )

    r = await A.sync_tiktok_account(_DB(results=[[]]), acc)

    assert acc.username == "keep" and acc.meta_data == {}
    assert events == [] and r.errors == []


@pytest.mark.asyncio
async def test_user_info_sparse_user_keeps_record_and_zero_fills_event(_tt_patches, monkeypatch):
    """A user object with only open_id must not blank the stored identity
    or add empty metadata. With stats scope the event still records 0
    followers, not None."""
    _Client.get = AsyncMock(return_value=_resp(200, {"data": {"user": {"open_id": "o1"}}}))
    events = []
    monkeypatch.setattr(A, "_persist_account_event", lambda db, acc, ts, et, m: events.append((et, m)))
    acc = _account(
        platform="tiktok", access_token_enc="enc", username="keep", display_name="Keep",
        avatar_url="https://old", meta_data={"x": 1}, scopes=["user.info.stats"],
    )

    await A.sync_tiktok_account(_DB(results=[[]]), acc)

    assert (acc.username, acc.display_name, acc.avatar_url) == ("keep", "Keep", "https://old")
    assert acc.meta_data == {"x": 1}
    assert events == [("profile_sync", {
        "followers_count": 0, "following_count": None, "likes_count": None,
        "video_count": None, "is_verified": None, "profile_deep_link": None,
        "api_source": "user.info",
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar", [
    _resp(200, {"logged_in": True}),
    _resp(503, {}),
])
async def test_empty_scrape_with_live_or_unreachable_sidecar_reports_nothing(
    _tt_patches, monkeypatch, sidecar
):
    """An empty grid only means "session expired" when the sidecar says it is
    logged out. A logged-in or unavailable sidecar gives no error."""
    monkeypatch.setattr(A, "decrypt_token", lambda _t: None)
    monkeypatch.setattr(A, "_scrape_tiktok_profile", lambda _u, _c: _scraped({}))
    _Client.get = AsyncMock(return_value=sidecar)
    acc = _account(platform="tiktok", username="cu", meta_data=dict(_COOKIES), scopes=[])

    r = await A.sync_tiktok_account(_DB(results=[[]]), acc)

    _Client.get.assert_awaited_once()
    assert "/session" in _Client.get.await_args.args[0]
    assert r.errors == []


# ── helpers ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_stats_picks_the_requested_id_from_the_batch():
    body = {"error": {"code": "ok"}, "data": {"videos": [
        {"id": "111", "view_count": 999, "like_count": 9},
        {"id": 222, "view_count": 40, "like_count": 4, "comment_count": 2, "share_count": 1},
    ]}}
    client = SimpleNamespace(post=AsyncMock(return_value=_resp(200, body)))

    out = await A._fetch_tiktok_video_stats(client, "tok", "222")

    assert (out.impressions, out.likes, out.comments, out.shares, out.reach) == (40, 4, 2, 1, 40)


@pytest.mark.asyncio
@pytest.mark.parametrize(("notes", "expected"), [
    (["tiktok_video_not_found", "tiktok_video_not_found"], 2),  # whole history is misses
    (["tiktok_video_not_found", None, "tiktok_video_not_found"], 1),  # streak broken
    ([], 0),
])
async def test_consecutive_misses_counts_trailing_streak(notes, expected):
    assert await A._tiktok_consecutive_misses(_DB(results=[notes]), uuid.uuid4(), "v") == expected


def test_scrape_without_cookies_passes_no_cookiefile(monkeypatch):
    seen = {}

    class _YDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def extract_info(self, url, download=False):
            seen["url"] = url
            return {"entries": [], "channel_follower_count": 3, "uploader_id": "u9"}

    mod = ModuleType("yt_dlp")
    mod.YoutubeDL = _YDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    unlinked = []
    monkeypatch.setattr(A.os, "unlink", unlinked.append)

    out = A._scrape_tiktok_profile("cu", {})

    assert "cookiefile" not in seen
    assert seen["url"] == "https://www.tiktok.com/@cu"
    assert out == {"videos": {}, "followers": 3, "channel_id": "u9"}
    assert unlinked == []
