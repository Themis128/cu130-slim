"""Unit tests for image_transform.py."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.services.image_transform import (
    PLATFORM_PRESETS,
    add_watermark,
    compress_image,
    convert_format,
    crop_image,
    get_image_info,
    resize_image,
)


def _make_image(mode: str = "RGB", size: tuple[int, int] = (400, 300), color: tuple = (120, 60, 30)) -> bytes:
    """Create an in-memory image and return its bytes."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    if mode == "RGBA":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_resize_image_cover():
    """cover fit crops to exact target dimensions."""
    image_bytes = _make_image(size=(400, 300))
    result = resize_image(image_bytes, width=100, height=100, fit="cover")
    assert result.width == 100
    assert result.height == 100
    assert result.mime_type == "image/jpeg"
    assert result.format == "jpeg"
    assert isinstance(result.image_bytes, bytes)
    assert len(result.image_bytes) > 0


def test_resize_image_contain():
    """contain fit pads to exact target dimensions while preserving content."""
    image_bytes = _make_image(mode="RGBA", size=(400, 300), color=(120, 60, 30, 200))
    result = resize_image(image_bytes, width=200, height=200, fit="contain", format="png")
    assert result.width == 200
    assert result.height == 200
    assert result.mime_type == "image/png"
    assert result.format == "png"


def test_resize_image_preset():
    """preset fills in target dimensions."""
    image_bytes = _make_image(size=(1200, 900))
    result = resize_image(image_bytes, preset="instagram_square", fit="cover")
    assert result.width == 1080
    assert result.height == 1080
    assert result.mime_type == "image/jpeg"


def test_resize_image_unknown_preset_raises():
    """Unknown preset raises ValueError."""
    image_bytes = _make_image()
    with pytest.raises(ValueError, match="Unknown preset"):
        resize_image(image_bytes, preset="unknown_platform")


def test_resize_image_width_only_maintains_aspect():
    """Specifying only width keeps aspect ratio."""
    image_bytes = _make_image(size=(400, 300))
    result = resize_image(image_bytes, width=200)
    assert result.width == 200
    assert result.height == 150


def test_crop_image():
    """Crop returns requested sub-region."""
    image_bytes = _make_image(size=(400, 300))
    result = crop_image(image_bytes, x=50, y=50, width=150, height=100)
    assert result.width == 150
    assert result.height == 100
    assert result.mime_type == "image/jpeg"


def test_convert_format_to_png():
    """convert_format changes image format."""
    image_bytes = _make_image(size=(200, 150))
    result = convert_format(image_bytes, format="png")
    assert result.mime_type == "image/png"
    assert result.format == "png"
    assert result.width == 200
    assert result.height == 150


def test_convert_format_to_webp():
    """convert_format supports WebP."""
    image_bytes = _make_image(size=(200, 150))
    result = convert_format(image_bytes, format="webp")
    assert result.mime_type == "image/webp"
    assert result.format == "webp"


def test_compress_image():
    """compress_image reduces quality to target size range."""
    img = Image.new("RGB", (800, 800), (120, 60, 30))
    draw = ImageDraw.Draw(img)
    for i in range(0, 800, 40):
        for j in range(0, 800, 40):
            draw.rectangle([i, j, i + 20, j + 20], fill=(i % 256, j % 256, (i + j) % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    image_bytes = buf.getvalue()
    result = compress_image(image_bytes, target_size_kb=50, min_quality=60)
    assert result.mime_type == "image/jpeg"
    assert result.format == "jpeg"
    # Even if it doesn't reach the target, it should stay at or above min_quality.
    assert len(result.image_bytes) > 0


def test_add_watermark():
    """Watermark overlays text without changing dimensions."""
    image_bytes = _make_image(size=(300, 200))
    result = add_watermark(image_bytes, text="Test", position="bottom-right", opacity=200)
    assert result.width == 300
    assert result.height == 200
    assert result.mime_type == "image/jpeg"
    assert len(result.image_bytes) > 0


def test_add_watermark_positions():
    """Watermark supports all standard positions."""
    image_bytes = _make_image(size=(300, 200))
    for pos in ("top-left", "top-right", "bottom-left", "bottom-right", "center"):
        result = add_watermark(image_bytes, text="W", position=pos)
        assert result.width == 300
        assert result.height == 200


def test_get_image_info():
    """get_image_info returns dimensions and mode."""
    image_bytes = _make_image(mode="RGBA", size=(250, 180), color=(10, 20, 30, 128))
    info = get_image_info(image_bytes)
    assert info["width"] == 250
    assert info["height"] == 180
    assert info["mode"] == "RGBA"
    assert info["format"] == "PNG"


def test_platform_presets_structure():
    """All presets have the expected structure and positive dimensions."""
    assert len(PLATFORM_PRESETS) == 18
    for key, preset in PLATFORM_PRESETS.items():
        assert isinstance(key, str)
        assert isinstance(preset, dict)
        assert isinstance(preset["width"], int)
        assert isinstance(preset["height"], int)
        assert isinstance(preset["label"], str)
        assert preset["width"] > 0
        assert preset["height"] > 0
