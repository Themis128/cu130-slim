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
