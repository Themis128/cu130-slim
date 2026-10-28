"""Unit tests for image_enhance.py."""
from __future__ import annotations

import io
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services import image_enhance
from app.services.image_enhance import (
    detect_subject_position,
    generate_alt_text,
    remove_background,
    remove_background_cf,
    score_image_quality,
    smart_crop,
    smart_crop_async,
    upscale_image,
)


def _make_image(mode: str = "RGB", size: tuple[int, int] = (120, 80), color: tuple = (128, 128, 128)) -> bytes:
    """Create an in-memory image and return its bytes."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    if mode == "RGBA":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_score_image_quality():
    """score_image_quality returns a populated ImageQualityScore."""
    image_bytes = _make_image(size=(100, 100), color=(128, 128, 128))
    score = score_image_quality(image_bytes)
    assert isinstance(score.overall, int)
    assert 0 <= score.overall <= 100
    assert isinstance(score.sharpness, int)
    assert isinstance(score.brightness, int)
    assert isinstance(score.contrast, int)
    assert isinstance(score.blur_detected, bool)
    assert isinstance(score.issues, list)


def test_upscale_image_2x():
    """Upscaling by 2x doubles dimensions."""
    image_bytes = _make_image(size=(100, 80))
    data, mime, w, h = upscale_image(image_bytes, scale=2)
    assert w == 200
    assert h == 160
    assert mime in ("image/jpeg", "image/png")
    assert isinstance(data, bytes)


def test_upscale_image_4x():
    """Upscaling by 4x quadruples dimensions."""
    image_bytes = _make_image(size=(50, 40))
    _, _, w, h = upscale_image(image_bytes, scale=4)
    assert w == 200
    assert h == 160


def test_upscale_image_invalid_scale():
    """Only 2x and 4x scales are supported."""
    image_bytes = _make_image()
    with pytest.raises(ValueError, match="Scale must be 2 or 4"):
        upscale_image(image_bytes, scale=3)


@pytest.mark.asyncio
async def test_remove_background_cf_no_credentials():
    """Cloudflare background removal returns None when not configured."""
    image_bytes = _make_image()
    with patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""), patch.object(
        image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", ""
    ):
        result = await remove_background_cf(image_bytes)
    assert result is None


@pytest.mark.asyncio
async def test_remove_background_cloudflare_success():
    """remove_background returns Cloudflare result when available."""
    image_bytes = _make_image()
    with patch.object(image_enhance, "remove_background_cf", new=AsyncMock(return_value=b"cf-png")):
        data, mime = await remove_background(image_bytes)
    assert data == b"cf-png"
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_remove_background_rembg_fallback():
    """remove_background falls back to rembg when Cloudflare is unavailable."""
    image_bytes = _make_image()
    fake_rembg = MagicMock(return_value=b"rembg-png")
    with (
        patch.object(image_enhance, "remove_background_cf", new=AsyncMock(return_value=None)),
        patch.dict(sys.modules, {"rembg": SimpleNamespace(remove=fake_rembg)}),
    ):
        data, mime = await remove_background(image_bytes)
    assert data == b"rembg-png"
    assert mime == "image/png"
    fake_rembg.assert_called_once_with(image_bytes)


def test_smart_crop_center():
    """smart_crop crops to target dimensions with a center subject."""
    image_bytes = _make_image(size=(400, 300))
    data, mime, w, h = smart_crop(image_bytes, 100, 100)
    assert w == 100
    assert h == 100
    assert mime == "image/jpeg"
    assert isinstance(data, bytes)


def test_smart_crop_subject_clamped():
    """smart_crop respects a subject position and clamps to image bounds."""
    image_bytes = _make_image(size=(400, 300))
    data, mime, w, h = smart_crop(image_bytes, 100, 100, subject_pos=(95, 95))
    assert w == 100
    assert h == 100
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_smart_crop_async_uses_detected_subject():
    """smart_crop_async passes detected subject position to smart_crop."""
    image_bytes = _make_image(size=(400, 300))
    with patch.object(image_enhance, "detect_subject_position", new=AsyncMock(return_value=(25, 25))):
        data, mime, w, h = await smart_crop_async(image_bytes, 100, 100)
    assert w == 100
    assert h == 100
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_detect_subject_position_no_credentials():
    """Subject detection returns None when DMR and Cloudflare both fail."""
    image_bytes = _make_image()
    with (
        patch.object(image_enhance, "_dmr_vision_query", new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", ""),
    ):
        result = await detect_subject_position(image_bytes)
    assert result is None


@pytest.mark.asyncio
async def test_generate_alt_text_cloudflare_success():
    """generate_alt_text returns alt text from Cloudflare when DMR returns None."""
    image_bytes = _make_image()

    class FakeResp:
        status_code = 200

        def json(self):
            return {"result": {"response": "A red apple on a table"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=FakeResp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(image_enhance, "_dmr_vision_query", new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok-123"),
        patch("app.services.image_enhance.httpx.AsyncClient", return_value=mock_client),
    ):
        alt = await generate_alt_text(image_bytes)
    assert alt == "A red apple on a table"


@pytest.mark.asyncio
async def test_generate_alt_text_cf_fallback():
    """generate_alt_text falls back to Cloudflare when DMR returns None."""
    image_bytes = _make_image()

    class CfOk:
        status_code = 200

        def json(self):
            return {"result": {"response": "CF alt text"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=CfOk())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(image_enhance, "_dmr_vision_query", new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok-123"),
        patch("app.services.image_enhance.httpx.AsyncClient", return_value=mock_client),
    ):
        alt = await generate_alt_text(image_bytes)
    assert alt == "CF alt text"


@pytest.mark.asyncio
async def test_generate_alt_text_no_credentials():
    """generate_alt_text returns None when DMR and CF both fail."""
    image_bytes = _make_image()
    with (
        patch.object(image_enhance, "_dmr_vision_query", new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", ""),
    ):
        alt = await generate_alt_text(image_bytes)
    assert alt is None


# ── coverage: quality score branches ──────────────────────────────────


def _gray_image(color: int, size: tuple = (64, 64)) -> bytes:
    img = Image.new("L", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_score_quality_l_mode_and_dark_bright_low_contrast():
    # L-mode path + too_dark + low contrast (uniform dark gray)
    s = score_image_quality(_gray_image(30))
    assert s.too_dark is True and s.too_bright is False
    assert s.blur_detected is True
    assert any("dark" in i for i in s.issues)
    assert any("contrast" in i.lower() for i in s.issues)

    # too_bright
    s2 = score_image_quality(_gray_image(240))
    assert s2.too_bright is True
    assert any("overexposed" in i for i in s2.issues)

    # sharp image -> no blur issue
    img = Image.new("L", (64, 64))
    px = img.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = 255 if (x + y) % 2 else 0
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    s3 = score_image_quality(buf.getvalue())
    assert s3.sharpness == 100 and s3.blur_detected is False


def test_upscale_rgba_png_and_bad_scale():
    data, mime, w, h = upscale_image(_make_image("RGBA", (40, 30)), scale=2)
    assert mime == "image/png" and (w, h) == (80, 60)


# ── remove_background_cf body ─────────────────────────────────────────


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload


class _Http:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        if self._exc:
            raise self._exc
        return self._resp



@pytest.mark.asyncio
async def test_remove_background_cf_mask_path(monkeypatch):
    # build a small mask image
    mask_img = Image.new("L", (8, 8), 255)
    mbuf = io.BytesIO()
    mask_img.save(mbuf, format="PNG")
    import base64 as b64
    payload = {"result": {"image": b64.b64encode(mbuf.getvalue()).decode()}}
    with (
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(_Resp(payload))),
    ):
        out = await remove_background_cf(_make_image("RGBA", (8, 8)))
    assert out is not None
    assert out[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_remove_background_cf_output_and_error_paths():
    import base64 as b64

    # "output" variant
    payload = {"result": {"output": b64.b64encode(b"pngbytes").decode()}}
    with (
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(_Resp(payload))),
    ):
        assert await remove_background_cf(_make_image(size=(8, 8))) == b"pngbytes"

    # non-200 -> warning + None
    with (
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(_Resp({"x": 1}, status=500, text="err"))),
    ):
        assert await remove_background_cf(_make_image(size=(8, 8))) is None

    # http exception -> warning + None
    with (
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(exc=RuntimeError("conn"))),
    ):
        assert await remove_background_cf(_make_image(size=(8, 8))) is None


@pytest.mark.asyncio
async def test_remove_background_cf_resizes_large_image():
    # image bigger than 1024 -> resize branch, then http error -> None
    with (
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(_Resp({}, status=500, text="e"))),
    ):
        assert await remove_background_cf(_make_image(size=(2000, 3000))) is None


@pytest.mark.asyncio
async def test_remove_background_rembg_none_and_import_error():
    fake = MagicMock(return_value=None)
    with (
        patch.object(image_enhance, "remove_background_cf", new=AsyncMock(return_value=None)),
        patch.dict(sys.modules, {"rembg": SimpleNamespace(remove=fake)}),
        pytest.raises(RuntimeError, match="returned None"),
    ):
        await remove_background(_make_image())

    with (
        patch.object(image_enhance, "remove_background_cf", new=AsyncMock(return_value=None)),
        patch.dict(sys.modules, {"rembg": None}),
        pytest.raises(RuntimeError, match="unavailable"),
    ):
        await remove_background(_make_image())


# ── _dmr_vision_query + _prepare_image_for_vision ─────────────────────


@pytest.mark.asyncio
async def test_dmr_vision_query_delegates(monkeypatch):
    called = {}

    async def _vision(uri, prompt, max_tokens=60):
        called.update(uri=uri, prompt=prompt, max_tokens=max_tokens)
        return "50,50"
    monkeypatch.setitem(sys.modules, "app.services.dmr",
                        SimpleNamespace(call_dmr_vision=_vision))
    out = await image_enhance._dmr_vision_query("data:x", "p", max_tokens=20)
    assert out == "50,50"
    assert called["max_tokens"] == 20


def test_prepare_image_for_vision_non_rgb_and_resize():
    # RGBA -> converts to RGB; >768px -> resizes
    uri = image_enhance._prepare_image_for_vision(_make_image("RGBA", (1500, 900)))
    assert uri.startswith("data:image/jpeg;base64,")
    uri2 = image_enhance._prepare_image_for_vision(_make_image(size=(100, 80)))
    assert uri2.startswith("data:image/jpeg;base64,")


# ── detect_subject_position DMR + CF branches ─────────────────────────


@pytest.mark.asyncio
async def test_detect_subject_dmr_success_and_bad_values(monkeypatch):
    with patch.object(image_enhance, "_dmr_vision_query",
                      new=AsyncMock(return_value="25,30")):
        assert await detect_subject_position(_make_image()) == (25, 30)

    # out-of-range values -> falls through to CF (no creds -> None)
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value="150,200")),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
    ):
        assert await detect_subject_position(_make_image()) is None

    # unparseable -> CF fallback
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value="no numbers")),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
    ):
        assert await detect_subject_position(_make_image()) is None

    # DMR raises -> CF fallback
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(side_effect=RuntimeError("down"))),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
    ):
        assert await detect_subject_position(_make_image()) is None


@pytest.mark.asyncio
async def test_detect_subject_cf_paths():
    # CF success
    ok = _Resp({"result": {"response": "60, 40"}})
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(ok)),
    ):
        assert await detect_subject_position(_make_image()) == (60, 40)

    # CF 200 but unparseable -> None
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(_Resp({"result": {"response": "none"}}))),
    ):
        assert await detect_subject_position(_make_image()) is None

    # CF raises -> None
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(exc=RuntimeError("cf down"))),
    ):
        assert await detect_subject_position(_make_image()) is None


# ── smart_crop tall image + RGBA ──────────────────────────────────────


def test_smart_crop_taller_and_rgba():
    # taller than target ratio -> crop height branch
    data, mime, w, h = smart_crop(_make_image(size=(200, 600)), 100, 100)
    assert (w, h) == (100, 100)

    # RGBA -> PNG output
    data, mime, w, h = smart_crop(_make_image("RGBA", (400, 300)), 100, 100)
    assert mime == "image/png"


# ── generate_alt_text DMR success / exceptions ────────────────────────


@pytest.mark.asyncio
async def test_alt_text_dmr_success_and_empty():
    with patch.object(image_enhance, "_dmr_vision_query",
                      new=AsyncMock(return_value='"A cat sleeps"')):
        assert await generate_alt_text(_make_image()) == "A cat sleeps"

    # empty after strip -> falls to CF (no creds -> None)
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value='""')),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", ""),
    ):
        assert await generate_alt_text(_make_image()) is None

    # DMR raises -> CF fallback
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(side_effect=RuntimeError("x"))),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
    ):
        assert await generate_alt_text(_make_image()) is None


@pytest.mark.asyncio
async def test_alt_text_cf_exception():
    with (
        patch.object(image_enhance, "_dmr_vision_query",
                     new=AsyncMock(return_value=None)),
        patch.object(image_enhance.settings, "CLOUDFLARE_ACCOUNT_ID", "acc"),
        patch.object(image_enhance.settings, "CLOUDFLARE_AI_API_TOKEN", "tok"),
        patch("app.services.image_enhance.httpx.AsyncClient",
              return_value=_Http(exc=RuntimeError("cf down"))),
    ):
        assert await generate_alt_text(_make_image()) is None


def test_apply_bg_mask_resizes_mismatched_mask():
    mask = Image.new("L", (4, 4), 255)
    mbuf = io.BytesIO()
    mask.save(mbuf, format="PNG")
    out = image_enhance._apply_bg_mask(
        _make_image(size=(40, 30)), mbuf.getvalue())
    assert out[:4] == b"\x89PNG"
