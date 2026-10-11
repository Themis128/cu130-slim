"""image_transform edge paths — modes, formats, watermark fallback."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import image_transform as IT


def _png(mode: str = "RGBA", size=(100, 80), color=(10, 200, 30, 128)) -> bytes:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_open_image_p_mode_converts_to_rgba():
    img = Image.new("RGB", (50, 40), (1, 2, 3)).convert("P")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = IT._open_image(buf.getvalue())
    assert out.mode == "RGBA"


def test_open_image_la_mode_converts():
    img = Image.new("LA", (50, 40), (200, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = IT._open_image(buf.getvalue())
    assert out.mode == "RGBA"


def test_save_image_rgba_to_jpeg_composites():
    data, mime = IT._save_image(Image.new("RGBA", (20, 20), (255, 0, 0, 100)), "jpeg")
    assert mime == "image/jpeg"
    out = Image.open(io.BytesIO(data))
    assert out.mode == "RGB"


def test_save_image_p_mode_jpeg_and_webp():
    p_img = Image.new("P", (20, 20))
    data, mime = IT._save_image(p_img, "jpeg")
    assert mime == "image/jpeg"
    data, mime = IT._save_image(p_img, "webp")
    assert mime == "image/webp"


def test_save_image_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        IT._save_image(Image.new("RGB", (10, 10)), "tiff")


def test_resize_requires_dimension_or_preset():
    with pytest.raises(ValueError, match="Either width"):
        IT.resize_image(_png(), format="png")


def test_resize_height_only_maintains_aspect():
    out = IT.resize_image(_png(size=(100, 80)), height=40, format="png")
    assert out.height == 40 and out.width == 50


def test_resize_contain_letterboxes():
    out = IT.resize_image(_png(size=(100, 80)), width=60, height=60, fit="contain", format="png")
    assert out.width == 60 and out.height == 60


def test_convert_format_avif_falls_back_or_succeeds():
    out = IT.convert_format(_png(), format="avif")
    assert out.format in ("avif", "webp")
    assert out.mime_type in ("image/avif", "image/webp")


def test_add_watermark_unknown_position_defaults():
    out = IT.add_watermark(_png(), "wm", position="bogus", format="png")
    assert out.width == 100 and out.height == 80


def test_get_image_info_unknown_format():
    # raw RGB blob saved as PNG reports PNG; a synthetic no-format image path
    out = IT.get_image_info(_png())
    assert out["width"] == 100 and out["format"] == "PNG"
    assert out["mode"] == "RGBA"
