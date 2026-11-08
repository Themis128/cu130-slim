"""Coverage for coach_chat — doctrine-on/off arms."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.coach as C

pytestmark = pytest.mark.asyncio


async def test_chat_with_doctrine(monkeypatch):
    monkeypatch.setattr(C, "doctrine_context", AsyncMock(return_value="doc1\n\ndoc2"))
    monkeypatch.setattr(C, "llm_call", AsyncMock(return_value="a reply over twenty chars"))
    out = await C.coach_chat(SimpleNamespace(message="q"), "t1", SimpleNamespace())
    assert out.reply == "a reply over twenty chars" and out.doctrine_used == 1


async def test_chat_no_doctrine_offline(monkeypatch):
    monkeypatch.setattr(C, "doctrine_context", AsyncMock(return_value=""))
    monkeypatch.setattr(C, "llm_call", AsyncMock(return_value=None))
    out = await C.coach_chat(SimpleNamespace(message="q"), "t1", SimpleNamespace())
    assert "offline" in out.reply and out.doctrine_used == 0
