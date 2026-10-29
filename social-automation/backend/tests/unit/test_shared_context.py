"""Coverage: shared_context load/save — redis, fallback file, defaults."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_load_from_redis(monkeypatch):
    from app.services import shared_context as M

    r = MagicMock()
    r.get = AsyncMock(return_value=json.dumps({"survey": {"goal": "g"}}))
    monkeypatch.setattr(M, "_redis", AsyncMock(return_value=r))
    assert (await M.load())["survey"]["goal"] == "g"


@pytest.mark.asyncio
async def test_load_fallback_file(monkeypatch, tmp_path):
    from app.services import shared_context as M

    p = tmp_path / "ctx.json"
    p.write_text(json.dumps({"offer": {"name": "X"}}))
    monkeypatch.setattr(M, "_redis", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(M, "FALLBACK", str(p))
    assert (await M.load())["offer"]["name"] == "X"


@pytest.mark.asyncio
async def test_load_default(monkeypatch):
    from app.services import shared_context as M

    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    monkeypatch.setattr(M, "_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(M, "FALLBACK", "/nonexistent/ctx.json")
    out = await M.load()
    assert out["survey"]["goal"].startswith("validate")


@pytest.mark.asyncio
async def test_save_to_redis(monkeypatch):
    from app.services import shared_context as M

    r = MagicMock()
    r.set = AsyncMock()
    monkeypatch.setattr(M, "_redis", AsyncMock(return_value=r))
    ctx = {"a": 1}
    await M.save(ctx)
    r.set.assert_awaited_once()
    assert "updated_at" in ctx


@pytest.mark.asyncio
async def test_save_fallback_file(monkeypatch, tmp_path):
    from app.services import shared_context as M

    p = tmp_path / "d" / "ctx.json"
    monkeypatch.setattr(M, "_redis", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(M, "FALLBACK", str(p))
    await M.save({"b": 2})
    assert json.loads(p.read_text())["b"] == 2
