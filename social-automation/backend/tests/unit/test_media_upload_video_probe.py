"""Upload-time video metadata probe in POST /media/upload."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api import media
from app.api.media import upload_media
from app.services import video_ai
from tests.unit.test_media_api_router import _DB, _asset, _team, _Upload, _user


def _video_asset(team, **kw):
    a = _asset(team_id=team.id, mime_type="video/mp4", filename="v.mp4")
    a.width = kw.get("width")
    a.height = kw.get("height")
    a.duration_seconds = kw.get("duration_seconds")
    a.meta_data = kw.get("meta_data")
    return a


@pytest.mark.asyncio
async def test_probe_fills_missing_dimensions_and_duration(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _video_asset(team, meta_data={"source": "upload"})
    probe = {"duration_seconds": 12.5, "width": 1080, "height": 1920, "codec": "h264"}
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    monkeypatch.setattr(video_ai, "probe_video", AsyncMock(return_value=probe))
    monkeypatch.setattr(media, "flag_modified", lambda *a: None)

    out = await upload_media(team.id, _Upload("v.mp4", b"vid", "video/mp4"), None, "", _user(), db)

    assert out is asset
    assert asset.duration_seconds == 12.5 and (asset.width, asset.height) == (1080, 1920)
    assert asset.meta_data == {"source": "upload", "video": probe}
    assert db.committed == 1


@pytest.mark.asyncio
async def test_probe_keeps_existing_values(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _video_asset(team, width=640, height=360, duration_seconds=3.0)
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    monkeypatch.setattr(video_ai, "probe_video", AsyncMock(return_value={"duration_seconds": 9, "width": 1}))
    monkeypatch.setattr(media, "flag_modified", lambda *a: None)

    await upload_media(team.id, _Upload("v.mp4", b"vid", "video/mp4"), None, "", _user(), db)

    assert (asset.width, asset.height, asset.duration_seconds) == (640, 360, 3.0)
    assert asset.meta_data["video"]["width"] == 1


@pytest.mark.asyncio
async def test_probe_failure_is_non_fatal(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _video_asset(team)
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    monkeypatch.setattr(video_ai, "probe_video", AsyncMock(side_effect=RuntimeError("ffprobe missing")))

    out = await upload_media(team.id, _Upload("v.mov", b"vid", "video/quicktime"), None, "", _user(), db)

    assert out is asset and asset.meta_data is None and db.committed == 0
