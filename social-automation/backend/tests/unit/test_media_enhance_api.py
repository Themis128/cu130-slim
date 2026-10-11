"""Tests for app/api/media_enhance.py — image enhancement endpoints."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.media_enhance as me
from app.models.content import StorageBackend


class _Res:
    def __init__(self, scalar):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
    def __init__(self, asset):
        self._asset = asset

    async def execute(self, q):
        return _Res(self._asset)


def _asset(**kw):
    base = dict(
        id=uuid.uuid4(), storage_backend=StorageBackend.local,
        storage_path="a/img.png", team_id=uuid.uuid4(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _transform_result(data=b"out", mime="image/png"):
    return SimpleNamespace(image_bytes=data, mime_type=mime)


# ── helpers ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_asset_found_and_404():
    asset = _asset()
    out = await me._get_asset(asset.id, _user(), _DB(asset))
    assert out is asset
    with pytest.raises(HTTPException) as e:
        await me._get_asset(uuid.uuid4(), _user(), _DB(None))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_load_asset_bytes_backends(monkeypatch):
    monkeypatch.setattr(me.r2_storage, "get_object", AsyncMock(return_value=b"r2"))
    assert await me._load_asset_bytes(_asset(storage_backend=StorageBackend.r2)) == b"r2"

    monkeypatch.setattr(me.minio_storage, "get_object", AsyncMock(return_value=b"minio"))
    assert await me._load_asset_bytes(_asset(storage_backend=StorageBackend.minio)) == b"minio"

    import app.core.path_utils as pu
    fake_path = SimpleNamespace(read_bytes=lambda: b"local")
    monkeypatch.setattr(pu, "safe_resolve", lambda base, p: fake_path)
    assert await me._load_asset_bytes(_asset()) == b"local"


def test_stream_response():
    resp = me._stream_response(b"abc", "image/webp")
    assert resp.media_type == "image/webp"


def _wire(monkeypatch, asset=None):
    asset = asset or _asset()
    monkeypatch.setattr(me, "_get_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(me, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    return asset


# ── endpoints ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_presets():
    out = await me.list_presets(_user())
    assert out["presets"] is me.PLATFORM_PRESETS


@pytest.mark.asyncio
async def test_get_asset_info(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "get_image_info", lambda b: {"width": 10, "height": 20, "mode": "RGB", "format": "PNG"})
    out = await me.get_asset_info(uuid.uuid4(), _user(), _DB(None))
    assert out["width"] == 10


@pytest.mark.asyncio
async def test_get_quality_score(monkeypatch):
    _wire(monkeypatch)
    score = {"overall": 80}
    monkeypatch.setattr(me, "score_image_quality", lambda b: score)
    assert await me.get_quality_score(uuid.uuid4(), _user(), _DB(None)) == score


@pytest.mark.asyncio
async def test_resize_ok_and_400(monkeypatch):
    _wire(monkeypatch)
    calls = {}

    def fake_resize(b, **kw):
        calls.update(kw)
        return _transform_result()

    monkeypatch.setattr(me, "resize_image", fake_resize)
    body = me.ResizeRequest(preset="instagram_post", format="webp", quality=90)
    resp = await me.resize_asset(uuid.uuid4(), body, _user(), _DB(None))
    assert resp.media_type == "image/png"
    assert calls["preset"] == "instagram_post"

    monkeypatch.setattr(me, "resize_image", Mock(side_effect=ValueError("bad dims")))
    with pytest.raises(HTTPException) as e:
        await me.resize_asset(uuid.uuid4(), body, _user(), _DB(None))
    assert e.value.status_code == 400 and "bad dims" in e.value.detail


@pytest.mark.asyncio
async def test_crop(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "crop_image", lambda *a: _transform_result(mime="image/jpeg"))
    body = me.CropRequest(x=0, y=0, width=10, height=10)
    resp = await me.crop_asset(uuid.uuid4(), body, _user(), _DB(None))
    assert resp.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_convert(monkeypatch):
    _wire(monkeypatch)
    seen = {}
    monkeypatch.setattr(me, "convert_format", lambda b, f, q: seen.update(fmt=f) or _transform_result(mime="image/webp"))
    resp = await me.convert_asset_format(uuid.uuid4(), me.ConvertFormatRequest(format="webp"), _user(), _DB(None))
    assert seen["fmt"] == "webp"
    assert resp.media_type == "image/webp"


@pytest.mark.asyncio
async def test_compress(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "compress_image", lambda *a: _transform_result())
    resp = await me.compress_asset(uuid.uuid4(), me.CompressRequest(target_size_kb=100), _user(), _DB(None))
    assert resp.media_type == "image/png"


@pytest.mark.asyncio
async def test_watermark(monkeypatch):
    _wire(monkeypatch)
    seen = {}

    def fake_wm(b, **kw):
        seen.update(kw)
        return _transform_result()

    monkeypatch.setattr(me, "add_watermark", fake_wm)
    body = me.WatermarkRequest(text="© cloudless", color=[255, 0, 0])
    resp = await me.watermark_asset(uuid.uuid4(), body, _user(), _DB(None))
    assert seen["color"] == (255, 0, 0)
    assert seen["text"] == "© cloudless"
    assert resp.media_type == "image/png"


@pytest.mark.asyncio
async def test_upscale_ok_and_400(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "upscale_image", lambda b, s: (b"up", "image/png", 20, 40))
    resp = await me.upscale_asset(uuid.uuid4(), me.UpscaleRequest(scale=2), _user(), _DB(None))
    assert resp.media_type == "image/png"

    monkeypatch.setattr(me, "upscale_image", Mock(side_effect=ValueError("scale must be 2 or 4")))
    with pytest.raises(HTTPException) as e:
        await me.upscale_asset(uuid.uuid4(), me.UpscaleRequest(scale=3), _user(), _DB(None))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_remove_bg_ok_and_503(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "remove_background", AsyncMock(return_value=(b"nb", "image/png")))
    resp = await me.remove_bg_asset(uuid.uuid4(), _user(), _DB(None))
    assert resp.media_type == "image/png"

    monkeypatch.setattr(me, "remove_background", AsyncMock(side_effect=RuntimeError("no AI")))
    with pytest.raises(HTTPException) as e:
        await me.remove_bg_asset(uuid.uuid4(), _user(), _DB(None))
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_smart_crop_ai_and_plain(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "smart_crop_async", AsyncMock(return_value=(b"ai", "image/png", 5, 5)))
    monkeypatch.setattr(me, "smart_crop", Mock(return_value=(b"plain", "image/jpeg", 5, 5)))
    resp = await me.smart_crop_asset(
        uuid.uuid4(), me.SmartCropRequest(target_width=5, target_height=5, use_ai=True), _user(), _DB(None))
    assert resp.media_type == "image/png"
    resp2 = await me.smart_crop_asset(
        uuid.uuid4(), me.SmartCropRequest(target_width=5, target_height=5, use_ai=False), _user(), _DB(None))
    assert resp2.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_alt_text_paths(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(me, "generate_alt_text", AsyncMock(return_value="a photo of a dogg"))
    import app.services.media_spellcheck as msc
    monkeypatch.setattr(msc, "correct_text", AsyncMock(return_value="a photo of a dog"))
    out = await me.generate_alt_text_endpoint(uuid.uuid4(), _user(), _DB(None))
    assert out["alt_text"] == "a photo of a dog"

    # correction unavailable -> fall back to raw alt text
    monkeypatch.setattr(msc, "correct_text", AsyncMock(return_value=None))
    out = await me.generate_alt_text_endpoint(uuid.uuid4(), _user(), _DB(None))
    assert out["alt_text"] == "a photo of a dogg"

    # no AI -> 503
    monkeypatch.setattr(me, "generate_alt_text", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as e:
        await me.generate_alt_text_endpoint(uuid.uuid4(), _user(), _DB(None))
    assert e.value.status_code == 503


# ── batch ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_enhance(monkeypatch):
    import app.worker.tasks.media_enhance as wme
    delay = Mock(return_value=SimpleNamespace(id="task-1"))
    monkeypatch.setattr(wme, "batch_enhance_task", SimpleNamespace(delay=delay))

    with pytest.raises(HTTPException) as e:
        await me.batch_enhance(me.BatchEnhanceRequest(asset_ids=[], operation="resize"), _user())
    assert e.value.status_code == 400

    many = [uuid.uuid4() for _ in range(51)]
    with pytest.raises(HTTPException) as e:
        await me.batch_enhance(me.BatchEnhanceRequest(asset_ids=many, operation="resize"), _user())
    assert e.value.status_code == 400

    ids = [uuid.uuid4(), uuid.uuid4()]
    out = await me.batch_enhance(
        me.BatchEnhanceRequest(asset_ids=ids, operation="upscale", params={"scale": 2}), _user())
    assert out == {"task_id": "task-1", "status": "queued", "asset_count": 2}
    assert delay.call_args[0][0] == [str(i) for i in ids]
    assert delay.call_args[0][1] == "upscale"
