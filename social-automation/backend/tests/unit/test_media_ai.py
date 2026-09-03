"""Unit tests for media AI auto-tagging helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import media_ai


@pytest.mark.asyncio
async def test_caption_image_dmr_success():
    with patch.object(
        media_ai, "_call_dmr_vision", new=AsyncMock(return_value="A sunset over mountains")
    ):
        caption = await media_ai._caption_image("data:image/png;base64,abc")
    assert caption == "A sunset over mountains"


@pytest.mark.asyncio
async def test_caption_image_dmr_fails_cf_fallback():
    with (
        patch.object(media_ai, "_call_dmr_vision", new=AsyncMock(return_value=None)),
        patch.object(media_ai, "_call_cloudflare_vision", new=AsyncMock(return_value={"description": "A lake in the woods"})),
    ):
        caption = await media_ai._caption_image("data:image/png;base64,abc")
    assert caption == "A lake in the woods"


@pytest.mark.asyncio
async def test_tag_image_cloudflare_success():
    with patch.object(
        media_ai,
        "_call_cloudflare_vision",
        new=AsyncMock(return_value={"description": "sunset, mountains, clouds"}),
    ):
        tags = await media_ai._tag_image("data:image/png;base64,abc")
    assert set(tags) == {"sunset", "mountains", "clouds"}


@pytest.mark.asyncio
async def test_tag_image_filters_instruction_words():
    with patch.object(
        media_ai,
        "_call_cloudflare_vision",
        new=AsyncMock(return_value={"description": "list, image, sunset, mountains, image"}),
    ):
        tags = await media_ai._tag_image("data:image/png;base64,abc")
    assert "list" not in tags
    assert "image" not in tags
    assert "sunset" in tags
    assert "mountains" in tags


def test_resize_for_vision_downscales_large_image():
    """Large images should be resized before sending to vision models."""
    import io

    from PIL import Image

    img = Image.new("RGB", (2000, 1000), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    resized, mime = media_ai._resize_for_vision(buf.getvalue(), max_edge=768)
    resized_img = Image.open(io.BytesIO(resized))
    assert max(resized_img.size) <= 768
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_extract_caption_prefers_description():
    assert media_ai._extract_caption({"description": "x"}) == "x"
    assert media_ai._extract_caption({"result": {"caption": "y"}}) == "y"
    assert media_ai._extract_caption({"text": "z"}) == "z"


@pytest.mark.asyncio
async def test_extract_caption_llama4_scout_response():
    """llama-4-scout returns result.response (chat completion format)."""
    result = {"result": {"response": "A blue square image", "choices": [{"message": {"content": "A blue square image"}}]}}
    assert media_ai._extract_caption(result) == "A blue square image"


@pytest.mark.asyncio
async def test_extract_caption_moondream_nested_result():
    """moondream returns result.result.caption."""
    result = {"result": {"result": {"caption": "A photo of a cat"}}}
    assert media_ai._extract_caption(result) == "A photo of a cat"


@pytest.mark.asyncio
async def test_extract_query_text_falls_back():
    assert media_ai._extract_query_text({"description": "x"}) == "x"
    assert media_ai._extract_query_text({"result": {"response": "y"}}) == "y"


@pytest.mark.asyncio
async def test_extract_query_text_moondream_answer():
    """moondream query returns result.result.answer."""
    result = {"result": {"result": {"answer": "Yes, there is a person"}}}
    assert media_ai._extract_query_text(result) == "Yes, there is a person"
