"""Edge-path tests for video_ai: ffprobe parsing fallbacks, frame-extraction
fallback, poster storage per backend, quality rollup failures, the
auto-tag early exits and the tag_video_by_id entrypoint.

ffprobe/ffmpeg are stubbed at the ``V._run`` seam; storage backends and
the vision chain are mocked, so nothing touches the network or GPU.
"""

from __future__ import annotations

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
from app.models.content import MediaAsset


def _jpeg(size=(64, 64), mode="RGB", color=128) -> bytes:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    fmt = "PNG" if mode in ("RGBA", "P", "L") else "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


def _proc(rc=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _asset(**kw) -> MediaAsset:
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        filename="v.mp4",
        mime_type="video/mp4",
        storage_backend="local",
        storage_path="clips/v.mp4",
        source="upload",
    )
    base.update(kw)
    return MediaAsset(**base)


class _Db:
    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1


# ── _parse_fps ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("0/0", None),
        ("abc/1", None),  # ValueError
        ("30/0", None),  # ZeroDivisionError
        ("0/1", None),  # fps not > 0
        ("25", 25.0),  # no denominator
        ("30000/1001", 29.97),
    ],
)
def test_parse_fps(raw, expected):
    assert V._parse_fps(raw) == expected


# ── probe_video ─────────────────────────────────────────────────────────


def _stub_probe(monkeypatch, payload=None, rc=0, exc=None):
    async def _run(cmd, timeout=0):
        if exc:
            raise exc
        out = json.dumps(payload).encode() if payload is not None else b""
        return _proc(rc=rc, stdout=out, stderr=b"boom")

    monkeypatch.setattr(V, "_run", _run)


class TestProbeEdges:
    @pytest.mark.asyncio
    async def test_nonzero_rc_returns_empty(self, monkeypatch):
        _stub_probe(monkeypatch, {"streams": []}, rc=1)
        assert await V.probe_video(b"x") == {}

    @pytest.mark.asyncio
    async def test_missing_binary_returns_empty(self, monkeypatch):
        _stub_probe(monkeypatch, exc=FileNotFoundError("ffprobe"))
        assert await V.probe_video(b"x") == {}

    @pytest.mark.asyncio
    async def test_empty_stdout_yields_empty_metadata(self, monkeypatch):
        _stub_probe(monkeypatch, None)
        assert await V.probe_video(b"x") == {}

    @pytest.mark.asyncio
    async def test_garbage_duration_and_bitrate_are_skipped(self, monkeypatch):
        _stub_probe(
            monkeypatch,
            {
                "streams": [{"codec_type": "video", "width": 640, "r_frame_rate": "24/1"}],
                "format": {"duration": "n/a", "bit_rate": "unknown"},
            },
        )
        out = await V.probe_video(b"x")
        # width present without height; fps from r_frame_rate fallback
        assert out == {"width": 640, "fps": 24.0}

    @pytest.mark.asyncio
    async def test_stream_duration_fallback_and_no_audio(self, monkeypatch):
        _stub_probe(
            monkeypatch,
            {
                "streams": [{"codec_type": "video", "height": 360, "duration": "4.6"}],
                "format": {"bit_rate": "0"},
            },
        )
        out = await V.probe_video(b"x")
        assert out == {"duration_seconds": 5, "height": 360}
        assert "has_audio" not in out and "bitrate" not in out


# ── extract_sample_frames ───────────────────────────────────────────────


class TestExtractEdges:
    @pytest.mark.asyncio
    async def test_all_seeks_fail_falls_back_to_thumbnail(self, monkeypatch):
        calls = []
        frame = _jpeg()

        async def _run(cmd, timeout=0):
            calls.append(cmd)
            if "thumbnail=90" in cmd:
                Path(cmd[-1]).write_bytes(frame)
                return _proc()
            return _proc(rc=1)

        monkeypatch.setattr(V, "_run", _run)
        frames = await V.extract_sample_frames(b"v", duration_seconds=10, max_frames=2)
        assert frames == [frame]
        # two seek attempts (max_frames=2) then the thumbnail fallback
        assert len(calls) == 3
        assert [c[3] for c in calls[:2]] == ["1.500", "5.000"]

    @pytest.mark.asyncio
    async def test_thumbnail_fallback_failure_returns_empty(self, monkeypatch):
        async def _run(cmd, timeout=0):
            return _proc(rc=0)  # rc ok but no output file written

        monkeypatch.setattr(V, "_run", _run)
        assert await V.extract_sample_frames(b"v", duration_seconds=None) == []


# ── poster / quality helpers ────────────────────────────────────────────


def test_pick_poster_unscorable_frame_loses(monkeypatch):
    good = _jpeg()
    # garbage bytes make score_image_quality raise → scored -1
    assert V.pick_poster_frame([b"not-an-image", good]) == good


def test_score_frames_all_unscorable_returns_empty():
    assert V._score_frames([b"junk", b"also junk"]) == {}


def test_score_frames_skips_bad_frame():
    q = V._score_frames([b"junk", _jpeg()])
    assert q["frames_sampled"] == 1


class TestStorePoster:
    @pytest.mark.asyncio
    async def test_minio(self, monkeypatch):
        up = AsyncMock(return_value={"public_url": "https://m/p.jpg"})
        monkeypatch.setattr(V.minio_storage, "upload_object", up)
        res = await V._store_poster(_asset(storage_backend="minio"), b"jpg")
        assert res == {"path": "clips/v.mp4.poster.jpg", "url": "https://m/p.jpg"}
        up.assert_awaited_once_with("clips/v.mp4.poster.jpg", b"jpg", content_type="image/jpeg")

    @pytest.mark.asyncio
    async def test_r2_without_public_url(self, monkeypatch):
        monkeypatch.setattr(V.r2_storage, "upload_object", AsyncMock(return_value={}))
        res = await V._store_poster(_asset(storage_backend="r2"), b"jpg")
        assert res == {"path": "clips/v.mp4.poster.jpg", "url": ""}

    @pytest.mark.asyncio
    async def test_local_with_public_base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(V, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(V.settings, "MEDIA_PUBLIC_BASE_URL", "https://cdn.example/")
        res = await V._store_poster(_asset(), b"jpg")
        assert res["url"] == ("https://cdn.example/api/v1/media/view?path=clips/v.mp4.poster.jpg")
        assert (tmp_path / "clips" / "v.mp4.poster.jpg").read_bytes() == b"jpg"

    @pytest.mark.asyncio
    async def test_local_without_public_base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(V, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(V.settings, "MEDIA_PUBLIC_BASE_URL", "")
        res = await V._store_poster(_asset(), b"jpg")
        assert res["url"] == "/api/v1/media/view?path=clips/v.mp4.poster.jpg"

    @pytest.mark.asyncio
    async def test_upload_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(V.minio_storage, "upload_object", AsyncMock(side_effect=RuntimeError("down")))
        assert await V._store_poster(_asset(storage_backend="minio"), b"jpg") is None


# ── frame_to_poster_jpeg ────────────────────────────────────────────────


def test_poster_jpeg_converts_mode_and_downscales():
    out = V.frame_to_poster_jpeg(_jpeg(size=(1440, 720), mode="RGBA"))
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and img.mode == "RGB"
    assert img.size == (720, 360)


def test_poster_jpeg_small_rgb_keeps_size():
    img = Image.open(io.BytesIO(V.frame_to_poster_jpeg(_jpeg(size=(100, 50)))))
    assert img.size == (100, 50)


# ── auto_tag_video_asset early exits / partial paths ────────────────────


class TestAutoTagEdges:
    @pytest.mark.asyncio
    async def test_no_bytes_returns_without_commit(self, monkeypatch):
        monkeypatch.setattr(MA, "_load_image_bytes", AsyncMock(return_value=None))
        probe = AsyncMock()
        monkeypatch.setattr(V, "probe_video", probe)
        db = _Db()
        await V.auto_tag_video_asset(_asset(), db)
        probe.assert_not_awaited()
        assert db.committed == 0

    @pytest.mark.asyncio
    async def test_empty_probe_and_no_frames_skips_commit(self, monkeypatch):
        monkeypatch.setattr(MA, "_load_image_bytes", AsyncMock(return_value=b"v"))
        monkeypatch.setattr(V, "probe_video", AsyncMock(return_value={}))
        monkeypatch.setattr(V, "extract_sample_frames", AsyncMock(return_value=[]))
        db = _Db()
        asset = _asset()
        await V.auto_tag_video_asset(asset, db)
        assert db.committed == 0
        assert asset.meta_data in (None, {})

    @pytest.mark.asyncio
    async def test_existing_dims_kept_poster_fail_no_quality_no_text(self, monkeypatch):
        frame = _jpeg()
        monkeypatch.setattr(MA, "_load_image_bytes", AsyncMock(return_value=b"v"))
        monkeypatch.setattr(
            V,
            "probe_video",
            AsyncMock(return_value={"duration_seconds": 9, "width": 1, "height": 2}),
        )
        monkeypatch.setattr(V, "extract_sample_frames", AsyncMock(return_value=[frame]))
        monkeypatch.setattr(V, "_score_frames", lambda frames: {})
        monkeypatch.setattr(V, "pick_poster_frame", lambda frames: None)  # → frames[0]
        monkeypatch.setattr(V, "_store_poster", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "_tag_image", AsyncMock(return_value=[]))
        chroma = AsyncMock()
        monkeypatch.setattr(V.chroma_client, "add_content", chroma)

        asset = _asset(duration_seconds=30, width=1080, height=1920)
        db = _Db()
        await V.auto_tag_video_asset(asset, db)

        # pre-existing dimensions are not overwritten by the probe
        assert (asset.duration_seconds, asset.width, asset.height) == (30, 1080, 1920)
        assert asset.meta_data["video"]["width"] == 1
        assert "poster_path" not in asset.meta_data
        assert "quality_score" not in asset.meta_data
        assert db.committed == 1
        chroma.assert_not_awaited()  # no caption/tags → nothing to index

    @pytest.mark.asyncio
    async def test_poster_without_url(self, monkeypatch):
        frame = _jpeg()
        monkeypatch.setattr(MA, "_load_image_bytes", AsyncMock(return_value=b"v"))
        monkeypatch.setattr(V, "probe_video", AsyncMock(return_value={}))
        monkeypatch.setattr(V, "extract_sample_frames", AsyncMock(return_value=[frame]))
        monkeypatch.setattr(V, "_store_poster", AsyncMock(return_value={"path": "p.jpg", "url": ""}))
        monkeypatch.setattr(MA, "_caption_image", AsyncMock(return_value=None))
        monkeypatch.setattr(MA, "_tag_image", AsyncMock(return_value=["a"]))
        monkeypatch.setattr("app.services.media_spellcheck.correct_tags", AsyncMock(side_effect=lambda t: t))
        chroma = AsyncMock()
        monkeypatch.setattr(V.chroma_client, "add_content", chroma)
        asset = _asset()
        await V.auto_tag_video_asset(asset, _Db())
        assert asset.meta_data["poster_path"] == "p.jpg"
        assert "poster_url" not in asset.meta_data
        assert asset.ai_tags == ["a"]
        chroma.assert_awaited_once_with(str(asset.team_id), str(asset.id), "a")


# ── tag_video_by_id ─────────────────────────────────────────────────────


def _factory(asset):
    class _Res:
        def scalar_one_or_none(self):
            return asset

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **kw):
            return _Res()

    return lambda: _Session()


class TestTagVideoById:
    @pytest.mark.asyncio
    async def test_string_id_found(self, monkeypatch):
        asset = _asset()
        tag = AsyncMock()
        monkeypatch.setattr(V, "auto_tag_video_asset", tag)
        await V.tag_video_by_id(str(asset.id), session_factory=_factory(asset))
        tag.assert_awaited_once()
        assert tag.await_args.args[0] is asset

    @pytest.mark.asyncio
    async def test_uuid_not_found(self, monkeypatch, caplog):
        tag = AsyncMock()
        monkeypatch.setattr(V, "auto_tag_video_asset", tag)
        await V.tag_video_by_id(uuid.uuid4(), session_factory=_factory(None))
        tag.assert_not_awaited()
        assert "not found" in caplog.text

    @pytest.mark.asyncio
    async def test_default_session_factory(self, monkeypatch):
        tag = AsyncMock()
        monkeypatch.setattr(V, "auto_tag_video_asset", tag)
        monkeypatch.setattr(V, "async_session_maker", _factory(None))
        await V.tag_video_by_id(uuid.uuid4())
        tag.assert_not_awaited()
