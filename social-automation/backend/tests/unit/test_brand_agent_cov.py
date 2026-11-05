"""Coverage for brand_agent.run_autopilot quality-gate branches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.brand_agent as BA

pytestmark = pytest.mark.asyncio


def _first(v):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: v), all=lambda: [])


async def test_no_brand_and_no_pillars():
    db = SimpleNamespace(execute=AsyncMock(return_value=_first(None)))
    out = await BA.run_autopilot(db, "t", "u")
    assert "No brand" in out["error"]


async def test_gate_branches(monkeypatch):
    brand = SimpleNamespace(id="b1", values=["v1"], name="B")
    voice = SimpleNamespace(messaging_pillars=[{"pillar": "p1"}])
    recent = SimpleNamespace(all=lambda: [])

    calls = {"n": 0}

    async def fake_gen(db, brand, voice, topic):
        calls["n"] += 1
        return ["delve synergy leverage paradigm unlock", "clean post about clouds"][calls["n"] - 1]

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _first(brand), _first(voice), recent, recent, recent, recent,
        ]),
        add=lambda o: None,
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        BA, "find_empty_calendar_slots",
        AsyncMock(return_value=[
            __import__("datetime").datetime(2026, 1, 1),
            __import__("datetime").datetime(2026, 1, 2)]))
    monkeypatch.setattr(BA, "generate_on_brand_content", fake_gen)
    import app.services.content_eval as ev
    monkeypatch.setattr(ev, "eval_post_async", AsyncMock(return_value=9))

    out = await BA.run_autopilot(db, "t", "u", days=1)
    assert out["skipped_count"] >= 1


async def test_eval_pass_creates(monkeypatch):
    brand = SimpleNamespace(id="b1", values=["v1"], name="B")
    voice = SimpleNamespace(messaging_pillars=[{"pillar": "p1"}])
    recent = SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_first(brand), _first(voice), recent, recent]),
        add=lambda o: None,
        commit=AsyncMock(),
    )
    monkeypatch.setattr(BA, "find_empty_calendar_slots", AsyncMock(return_value=[__import__("datetime").datetime(2026,1,1)]))
    monkeypatch.setattr(BA, "generate_on_brand_content", AsyncMock(return_value="clean short copy"))
    import app.services.content_eval as ev
    monkeypatch.setattr(ev, "eval_post_async", AsyncMock(return_value=9))
    out = await BA.run_autopilot(db, "t", "u", days=1)
    assert out["created_count"] == 1
