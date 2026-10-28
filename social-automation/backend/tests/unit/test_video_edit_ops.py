"""Tests for the video-edit ops (#908): app/services/video_ai.py trim/convert/
attach-audio/mute/extract and their app/api/media_enhance.py endpoints.

ffmpeg is stubbed at the ``V._run`` seam, so no binary is needed in CI.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.media_enhance as me
import app.services.video_ai as V

# ── service: ffmpeg seam ─────────────────────────────────────────────


def _ok_run(seen: list):
    async def _run(cmd, timeout=0):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"OUT:" + Path(cmd[-1]).suffix.encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return _run


async def _fail_run(cmd, timeout=0):
    return subprocess.CompletedProcess(cmd, 1, b"", b"boom")


@pytest.mark.asyncio
async def test_run_op_maps_missing_binary_and_timeout(monkeypatch):
    async def missing(cmd, timeout=0):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(V, "_run", missing)
    r = await V._run_op(["ffmpeg"], timeout=1)
    assert r.returncode == 127

    async def slow(cmd, timeout=0):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(V, "_run", slow)
    r = await V._run_op(["ffmpeg"], timeout=1)
    assert r.returncode == 124
    assert r.stderr == b"timeout"


@pytest.mark.asyncio
async def test_trim_video(monkeypatch):
    seen: list = []
    monkeypatch.setattr(V, "_run", _ok_run(seen))
    assert await V.trim_video(b"v", 1.0, 3.5, mime="video/webm") == b"OUT:.mp4"
    cmd = seen[0]
    assert cmd[cmd.index("-ss") + 1] == "1.000"
    assert cmd[cmd.index("-t") + 1] == "2.500"
    assert cmd[cmd.index("-i") + 1].endswith(".webm")
    # invalid ranges never call ffmpeg
    assert await V.trim_video(b"v", 3.0, 3.0) is None
    assert await V.trim_video(b"v", -1.0, 3.0) is None
    assert len(seen) == 1
    monkeypatch.setattr(V, "_run", _fail_run)
    assert await V.trim_video(b"v", 0.0, 1.0) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fmt,codec,faststart",
    [
        ("mp4", "libx264", True),
        (".MOV", "libx264", False),
        ("webm", "libvpx-vp9", False),
    ],
)
async def test_convert_video(monkeypatch, fmt, codec, faststart):
    seen: list = []
    monkeypatch.setattr(V, "_run", _ok_run(seen))
    ext = fmt.lower().lstrip(".")
    assert await V.convert_video(b"v", fmt) == f"OUT:.{ext}".encode()
    cmd = seen[0]
    assert codec in cmd
    assert ("+faststart" in cmd) is faststart


@pytest.mark.asyncio
async def test_convert_video_rejects_and_fails(monkeypatch):
    assert await V.convert_video(b"v", "gif") is None
    monkeypatch.setattr(V, "_run", _fail_run)
    assert await V.convert_video(b"v", "mp4") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("loop", [True, False])
async def test_attach_audio_success(monkeypatch, loop):
    seen: list = []
    monkeypatch.setattr(V, "_run", _ok_run(seen))
    out = await V.attach_audio(b"v", b"a", audio_mime="audio/wav", loop_audio=loop)
    assert out == b"OUT:.mp4"
    cmd = seen[0]
    assert ("-stream_loop" in cmd) is loop
    assert any(str(c).endswith(".wav") for c in cmd)
    assert "-shortest" in cmd


@pytest.mark.asyncio
async def test_mute_and_extract_success(monkeypatch):
    seen: list = []
    monkeypatch.setattr(V, "_run", _ok_run(seen))
    assert await V.mute_video(b"v") == b"OUT:.mp4"
    assert "-an" in seen[0]
    assert await V.extract_audio(b"v") == b"OUT:.mp3"
    assert "-vn" in seen[1]


@pytest.mark.asyncio
async def test_edit_ops_fail_gracefully(monkeypatch):
    monkeypatch.setattr(V, "_run", _fail_run)
    assert await V.attach_audio(b"v", b"a") is None
    assert await V.mute_video(b"v") is None
    assert await V.extract_audio(b"v") is None


# ── API endpoints ────────────────────────────────────────────────────


def _asset(mime="video/mp4"):
    return SimpleNamespace(id=uuid.uuid4(), mime_type=mime)


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _wire(monkeypatch, *assets):
    monkeypatch.setattr(me, "_get_asset", AsyncMock(side_effect=list(assets)))
    monkeypatch.setattr(me, "_load_asset_bytes", AsyncMock(return_value=b"bytes"))


@pytest.mark.asyncio
async def test_ensure_video_rejects_non_video():
    await me._ensure_video(_asset("video/mp4"))
    for mime in ("image/png", None):
        with pytest.raises(HTTPException) as e:
            await me._ensure_video(_asset(mime))
        assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_video_trim_endpoint(monkeypatch):
    _wire(monkeypatch, _asset(), _asset())
    monkeypatch.setattr(V, "trim_video", AsyncMock(side_effect=[b"cut", None]))
    body = me.VideoTrimRequest(start=0, end=2)
    resp = await me.video_trim(uuid.uuid4(), body, _user(), None)
    assert resp.media_type == "video/mp4"
    with pytest.raises(HTTPException) as e:
        await me.video_trim(uuid.uuid4(), body, _user(), None)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_video_convert_endpoint(monkeypatch):
    _wire(monkeypatch, _asset(), _asset())
    monkeypatch.setattr(V, "convert_video", AsyncMock(side_effect=[b"webm", None]))
    resp = await me.video_convert(uuid.uuid4(), me.VideoConvertRequest(format=".WEBM"), _user(), None)
    assert resp.media_type == "video/webm"
    with pytest.raises(HTTPException) as e:
        await me.video_convert(uuid.uuid4(), me.VideoConvertRequest(format="gif"), _user(), None)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_video_attach_audio_endpoint(monkeypatch):
    body = me.VideoAttachAudioRequest(audio_asset_id=uuid.uuid4(), loop_audio=False)
    mux = AsyncMock(side_effect=[b"muxed", None])
    monkeypatch.setattr(V, "attach_audio", mux)

    _wire(monkeypatch, _asset(), _asset("audio/mpeg"))
    resp = await me.video_attach_audio(uuid.uuid4(), body, _user(), None)
    assert resp.media_type == "video/mp4"
    assert mux.await_args.kwargs["loop_audio"] is False
    assert mux.await_args.kwargs["audio_mime"] == "audio/mpeg"

    _wire(monkeypatch, _asset(), _asset("audio/mpeg"))
    with pytest.raises(HTTPException) as e:
        await me.video_attach_audio(uuid.uuid4(), body, _user(), None)
    assert e.value.status_code == 422

    _wire(monkeypatch, _asset(), _asset("image/png"))
    with pytest.raises(HTTPException) as e:
        await me.video_attach_audio(uuid.uuid4(), body, _user(), None)
    assert "not an audio asset" in e.value.detail


@pytest.mark.asyncio
async def test_video_mute_and_extract_endpoints(monkeypatch):
    _wire(monkeypatch, _asset(), _asset(), _asset(), _asset())
    monkeypatch.setattr(V, "mute_video", AsyncMock(side_effect=[b"m", None]))
    monkeypatch.setattr(V, "extract_audio", AsyncMock(side_effect=[b"a", None]))
    assert (await me.video_mute(uuid.uuid4(), _user(), None)).media_type == "video/mp4"
    with pytest.raises(HTTPException):
        await me.video_mute(uuid.uuid4(), _user(), None)
    assert (await me.video_extract_audio(uuid.uuid4(), _user(), None)).media_type == "audio/mpeg"
    with pytest.raises(HTTPException) as e:
        await me.video_extract_audio(uuid.uuid4(), _user(), None)
    assert e.value.status_code == 422
