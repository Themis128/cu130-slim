"""Tests for /media/view format=jpeg re-encode helper."""

from __future__ import annotations

import io

from PIL import Image

from app.api.media import _reencode_image_bytes


def _png_rgba_bytes() -> bytes:
    img = Image.new("RGBA", (32, 24), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _webp_bytes() -> bytes:
    img = Image.new("RGB", (40, 30), (0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def test_reencode_png_to_jpeg_strips_alpha():
    raw, mime = _reencode_image_bytes(_png_rgba_bytes(), out_format="JPEG")
    assert mime == "image/jpeg"
    out = Image.open(io.BytesIO(raw))
    assert out.format == "JPEG"
    assert out.mode == "RGB"
    assert out.size == (32, 24)


def test_reencode_webp_to_jpeg():
    raw, mime = _reencode_image_bytes(_webp_bytes(), out_format="JPEG")
    assert mime == "image/jpeg"
    out = Image.open(io.BytesIO(raw))
    assert out.format == "JPEG"
    assert out.size == (40, 30)


def test_reencode_default_png():
    raw, mime = _reencode_image_bytes(_webp_bytes(), out_format="PNG")
    assert mime == "image/png"
    out = Image.open(io.BytesIO(raw))
    assert out.format == "PNG"
