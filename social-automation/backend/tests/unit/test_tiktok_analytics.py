"""Unit tests for TikTok analytics sync (Display API video/query)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import analytics_sync as sync
from app.services.tiktok_api import is_tiktok_publish_id


def test_is_tiktok_publish_id():
    assert is_tiktok_publish_id("v_inbox_file~v2.7685427808466847766") is True
    assert is_tiktok_publish_id("p_pub_photo~v2.1") is True
    assert is_tiktok_publish_id("7481122334455") is False
    assert is_tiktok_publish_id("") is False
    assert is_tiktok_publish_id(None) is False


def test_resolve_tiktok_display_video_id_prefers_public_id():
    post = SimpleNamespace(
        platform_specific={
            "tiktok": {
                "publish_id": "v_inbox_file~v2.1",
                "publicaly_available_post_id": "999888777",
            }
        }
    )
    target = SimpleNamespace(platform_post_id="v_inbox_file~v2.1", post=post)
    assert sync._resolve_tiktok_display_video_id(target) == "999888777"


def test_resolve_tiktok_display_video_id_skips_inbox_publish_id():
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_id": "v_inbox_file~v2.1"}})
    target = SimpleNamespace(platform_post_id="v_inbox_file~v2.1", post=post)
    assert sync._resolve_tiktok_display_video_id(target) is None


def test_resolve_tiktok_display_video_id_uses_platform_post_id():
    post = SimpleNamespace(platform_specific={})
    target = SimpleNamespace(platform_post_id="7481122334455", post=post)
    assert sync._resolve_tiktok_display_video_id(target) == "7481122334455"


@pytest.mark.asyncio
async def test_fetch_tiktok_video_stats_uses_post_query():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "error": {"code": "ok"},
        "data": {
            "videos": [
                {
                    "id": "7481122334455",
                    "view_count": 10,
                    "like_count": 2,
                    "comment_count": 1,
                    "share_count": 0,
                }
            ]
        },
    }
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)

    metrics = await sync._fetch_tiktok_video_stats(client, "tok", "7481122334455")

    assert metrics.impressions == 10
    assert metrics.likes == 2
    assert metrics.comments == 1
    client.post.assert_awaited_once()
    kwargs = client.post.await_args.kwargs
    assert kwargs["params"]["fields"].startswith("id,view_count")
    assert kwargs["json"] == {"filters": {"video_ids": ["7481122334455"]}}
    assert "video/query/" in client.post.await_args.args[0]


def _tt_account(scopes: list[str]) -> SimpleNamespace:
    import uuid as _uuid

    return SimpleNamespace(
        id=_uuid.uuid4(),
        team_id=_uuid.uuid4(),
        platform="tiktok",
        username="cloudless.gr",
        display_name="Cloudless",
        avatar_url=None,
        access_token_enc="enc",
        scopes=scopes,
        meta_data={"tiktok_web_cookies": {"sessionid": "x"}},
    )


def _mock_db():
    db = MagicMock()
    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=empty)
    db.commit = AsyncMock()
    return db


def _mock_http(monkeypatch, user: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"user": user}}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=client)
    acm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(sync.httpx, "AsyncClient", lambda *a, **k: acm)
    return client


@pytest.mark.asyncio
async def test_tiktok_profile_only_grant_keeps_scraped_followers(monkeypatch):
    """Profile scope without stats must not record a zero-follower API event
    or suppress the scraper event — the false-drop regression."""
    account = _tt_account(["user.info.basic", "user.info.profile", "video.list"])
    db = _mock_db()
    _mock_http(monkeypatch, {
        "username": "cloudless.gr",
        "bio_description": "bio",
        "is_verified": False,
        # no follower_count — stats scope absent
    })
    monkeypatch.setattr(sync, "decrypt_token", lambda _e: "tok")
    monkeypatch.setattr(sync, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(
        sync, "_scrape_tiktok_profile",
        lambda _u, _c: {"followers": 40, "channel_id": "c1",
                        "videos": {"v1": sync.MetricBundle(impressions=1)}},
    )

    await sync.sync_tiktok_account(db, account)

    events = [c.args[0] for c in db.add.call_args_list]
    assert len(events) == 1
    meta = events[0].meta_data
    assert meta["followers_count"] == 40
    assert "api_source" not in meta
    assert account.meta_data["bio_description"] == "bio"


@pytest.mark.asyncio
async def test_tiktok_stats_grant_writes_authoritative_event(monkeypatch):
    """With user.info.stats the API event is authoritative and the scrape
    event is suppressed."""
    account = _tt_account(["user.info.basic", "user.info.stats", "video.list"])
    db = _mock_db()
    _mock_http(monkeypatch, {
        "username": "cloudless.gr",
        "follower_count": 25,
        "following_count": 13,
        "likes_count": 3,
        "video_count": 1,
    })
    monkeypatch.setattr(sync, "decrypt_token", lambda _e: "tok")
    monkeypatch.setattr(sync, "_persist_snapshot", AsyncMock())
    monkeypatch.setattr(
        sync, "_scrape_tiktok_profile",
        lambda _u, _c: {"followers": 40, "channel_id": "c1",
                        "videos": {"v1": sync.MetricBundle(impressions=1)}},
    )

    await sync.sync_tiktok_account(db, account)

    events = [c.args[0] for c in db.add.call_args_list]
    assert len(events) == 1
    meta = events[0].meta_data
    assert meta["api_source"] == "user.info"
    assert meta["followers_count"] == 25
