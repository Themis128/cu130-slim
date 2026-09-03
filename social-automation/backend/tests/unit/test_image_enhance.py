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
