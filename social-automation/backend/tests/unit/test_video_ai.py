"""Unit tests for video_ai — ffprobe/ffmpeg seams stubbed at to_thread,
vision helpers stubbed at the DMR/CF boundary; PIL frames are real so the
poster picker exercises the real quality scorer.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import app.services.media_ai as MA
import app.services.video_ai as V


def _frame_bytes(color: int = 128, noise: bool = False, size: int = 512) -> bytes:
    img = Image.new("L", (size, size), color)
    if noise:
        import random

        random.seed(7)
        img.putdata([random.randint(0, 255) for _ in range(size * size)])
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


# Realistic sizes — the Laplacian scorer's border artifact inflates
# variance on tiny flat images, so "bad" frames must be genuinely dark.
_SHARP = _frame_bytes(noise=True)
_DARK = _frame_bytes(color=20)


_FFPROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 704,
                "height": 1280,
                "avg_frame_rate": "30000/1001",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "12.34", "bit_rate": "1500000"},
    }
).encode()


def _proc(rc: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _stub_ffmpeg_frames(monkeypatch, frames: list[bytes]):
    """to_thread seam: write the ffmpeg output file for each -frames:v call."""
    calls: list[list[str]] = []

    async def _tt(func, cmd, **kw):
        calls.append(cmd)
        dst = Path(cmd[-1])
        if "ffprobe" in cmd[0]:
            return _proc(stdout=_FFPROBE_JSON)
        idx = len([c for c in calls if c[0] == "ffmpeg"]) - 1
        dst.write_bytes(frames[min(idx, len(frames) - 1)])
        return _proc()

    monkeypatch.setattr(asyncio, "to_thread", _tt)
    return calls


class TestProbe:
    @pytest.mark.asyncio
    async def test_parses_metadata(self, monkeypatch):
        async def _tt(func, cmd, **kw):
            return _proc(stdout=_FFPROBE_JSON)

        monkeypatch.setattr(asyncio, "to_thread", _tt)
        out = await V.probe_video(b"mp4")
        assert out["duration_seconds"] == 12
        assert out["width"] == 704 and out["height"] == 1280
        assert out["fps"] == pytest.approx(29.97, abs=0.01)
        assert out["video_codec"] == "h264"
        assert out["has_audio"] is True
        assert out["bitrate"] == 1500000

    @pytest.mark.asyncio
    async def test_ffprobe_failure_returns_empty(self, monkeypatch):
        async def _tt(func, cmd, **kw):
            return _proc(rc=1, stderr=b"moov atom not found")

        monkeypatch.setattr(asyncio, "to_thread", _tt)
        assert await V.probe_video(b"junk") == {}

    @pytest.mark.asyncio
    async def test_missing_binary_returns_empty(self, monkeypatch):
        async def _tt(func, cmd, **kw):
            raise FileNotFoundError("ffprobe")

        monkeypatch.setattr(asyncio, "to_thread", _tt)
        assert await V.probe_video(b"mp4") == {}


class TestExtractFrames:
    @pytest.mark.asyncio
    async def test_evenly_spaced_seek_positions(self, monkeypatch):
        calls = _stub_ffmpeg_frames(monkeypatch, [_SHARP, _DARK, _SHARP])
        frames = await V.extract_sample_frames(b"mp4", duration_seconds=10.0)
        assert len(frames) == 3
        seeks = [c[c.index("-ss") + 1] for c in calls if "-ss" in c]
        assert seeks == ["1.500", "5.000", "8.500"]

    @pytest.mark.asyncio
    async def test_thumbnail_fallback_when_duration_unknown(self, monkeypatch):
        calls = _stub_ffmpeg_frames(monkeypatch, [_SHARP])
        frames = await V.extract_sample_frames(b"mp4", duration_seconds=None)
        assert len(frames) == 1
        assert any("thumbnail" in " ".join(c) for c in calls)

    @pytest.mark.asyncio
    async def test_failed_extract_returns_empty(self, monkeypatch):
        async def _tt(func, cmd, **kw):
            return _proc(rc=1, stderr=b"bad")

        monkeypatch.setattr(asyncio, "to_thread", _tt)
        assert await V.extract_sample_frames(b"mp4", duration_seconds=5.0) == []


class TestPoster:
    def test_picks_sharpest_frame(self):
        assert V.pick_poster_frame([_DARK, _SHARP, _DARK]) == _SHARP

    def test_empty_returns_none(self):
        assert V.pick_poster_frame([]) is None


def _asset(tmp_path):
    from app.models.content import MediaAsset

    if tmp_path.is_dir():
        (tmp_path / "v.mp4").write_bytes(b"mp4")
    return MediaAsset(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        filename="v.mp4",
        mime_type="video/mp4",
        storage_backend="local",
        storage_path="v.mp4",
        source="upload",
    )


class _Db:
    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1


class TestAutoTagVideo:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, monkeypatch, tmp_path):
        monkeypatch.setattr(V, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        asset = _asset(tmp_path)
        db = _Db()
        _stub_ffmpeg_frames(monkeypatch, [_DARK, _SHARP, _DARK])
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value="A clip"))
        monkeypatch.setattr(
            MA,
            "_tag_image",
            AsyncMock(side_effect=[["sky", "cloud"], ["cloud", "server", "sky"]]),
        )
        chroma = AsyncMock()
        monkeypatch.setattr(V.chroma_client, "add_content", chroma)

        await V.auto_tag_video_asset(asset, db)

        assert asset.duration_seconds == 12
        assert asset.width == 704 and asset.height == 1280
        assert asset.meta_data["video"]["video_codec"] == "h264"
        assert asset.ai_caption == "A clip"
        # tags merged across frames, deduped
        assert asset.ai_tags == ["sky", "cloud", "server"]
        assert asset.meta_data["quality_score"]["frames_sampled"] == 3
        assert asset.embedding_id == str(asset.id)
        # poster persisted beside the video and referenced in meta
        poster = asset.meta_data["poster_path"]
        assert poster.endswith(".poster.jpg")
        assert (tmp_path / "v.mp4.poster.jpg").is_file()
        chroma.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dark_video_flagged(self, monkeypatch, tmp_path):
        monkeypatch.setattr(V, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        asset = _asset(tmp_path)
        _stub_ffmpeg_frames(monkeypatch, [_DARK, _DARK, _DARK])
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "_tag_image", AsyncMock(return_value=[]))
        monkeypatch.setattr(V.chroma_client, "add_content", AsyncMock())

        await V.auto_tag_video_asset(asset, _Db())
        assert asset.meta_data["quality_failed"] is True
        assert asset.ai_caption is None  # vision unavailable → no caption

    @pytest.mark.asyncio
    async def test_no_frames_still_saves_probe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(V, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(MA, "UPLOAD_DIR", str(tmp_path))
        asset = _asset(tmp_path)

        async def _tt(func, cmd, **kw):
            if "ffprobe" in cmd[0]:
                return _proc(stdout=_FFPROBE_JSON)
            return _proc(rc=1, stderr=b"dead")

        monkeypatch.setattr(asyncio, "to_thread", _tt)
        await V.auto_tag_video_asset(asset, _Db())
        assert asset.duration_seconds == 12
        assert asset.meta_data["video"]["fps"] == pytest.approx(29.97, abs=0.01)
        assert "poster_path" not in asset.meta_data


class TestAutoTagRouting:
    @pytest.mark.asyncio
    async def test_video_routes_to_video_ai(self, monkeypatch):
        asset = _asset(Path("/nonexistent"))
        called = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return asset

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **kw):
                return _Result()

            async def commit(self):
                pass

        monkeypatch.setattr("app.services.video_ai.auto_tag_video_asset", called)
        await MA.auto_tag_asset(asset.id, session_factory=lambda: _Session())
        called.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audio_still_skipped(self, monkeypatch):
        asset = _asset(Path("/nonexistent"))
        asset.mime_type = "audio/mpeg"
        called = AsyncMock()

        class _Result:
            def scalar_one_or_none(self):
                return asset

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **kw):
                return _Result()

        monkeypatch.setattr("app.services.video_ai.auto_tag_video_asset", called)
        await MA.auto_tag_asset(asset.id, session_factory=lambda: _Session())
        called.assert_not_awaited()
