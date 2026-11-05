"""Coverage for image_enhance — hex helper, add_background, CF output branch."""

import io

import pytest
from PIL import Image

import app.services.image_enhance as IE

pytestmark = pytest.mark.asyncio


def _png(rgba=True):
    img = Image.new("RGBA" if rgba else "RGB", (40, 40), (200, 30, 30, 128) if rgba else (200, 30, 30))
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def test_hex_to_rgb():
    assert IE._hex_to_rgb("#FF8000") == (255, 128, 0)
    assert IE._hex_to_rgb("ffffff") == (255, 255, 255)


def test_add_background_styles():
    out, mime = IE.add_background(_png(), style="gradient")
    assert mime == "image/jpeg" and len(out) > 100
    out2, _ = IE.add_background(_png(), style="solid", color_top="#112233", spotlight=False)
    assert len(out2) > 100
    out3, _ = IE.add_background(_png(rgba=False))
    assert len(out3) > 100
