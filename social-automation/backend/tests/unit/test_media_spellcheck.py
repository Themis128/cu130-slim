"""Unit tests for app/services/media_spellcheck.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.services.media_spellcheck as MS


@pytest.fixture
def ac(monkeypatch):
    m = AsyncMock(side_effect=lambda t, language=None: f"{t}!")
    monkeypatch.setattr(MS, "auto_correct", m)
    return m


@pytest.mark.asyncio
async def test_correct_text_passthrough(ac):
    assert await MS.correct_text(None) is None
    assert await MS.correct_text("") == ""
    ac.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_text_corrects(ac):
    assert await MS.correct_text("helo") == "helo!"
    ac.assert_awaited_once_with("helo", language="en-US")


@pytest.mark.asyncio
async def test_correct_tags(ac):
    # multi-word tags corrected; single tokens pass through; empties dropped
    out = await MS.correct_tags(
        ["  multi word  ", "comfyui", "  ", "Single", "single"])
    assert out == ["multi word!", "comfyui", "Single"]
    ac.assert_awaited_once_with("multi word", language="en-US")


@pytest.mark.asyncio
async def test_correct_tags_none(ac):
    assert await MS.correct_tags(None) == []
    assert await MS.correct_tags([]) == []
