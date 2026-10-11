"""Unit tests for app/services/brand_agent.py — autonomous brand content agent."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.brand_agent as A


class _Res:
    def __init__(self, first=None):
        self._first = first

    def scalars(self):
        return self


class _ResFirst(_Res):
    def first(self):
        return self._first


class _DB:
    def __init__(self, results):
        self.results = list(results)  # deque of _Res
        self.i = 0
        self.added = []
        self.commits = 0

    async def execute(self, *a, **kw):
        r = self.results[self.i % len(self.results)]
        self.i += 1
        return r

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        obj.id = obj.id or uuid.uuid4()


def _brand(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="Cloudless",
        positioning_statement="pos",
        mission="m",
        values=["speed"],
        tagline="tag",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _voice(**kw):
    base = dict(
        tone_dimensions={},
        banned_phrases=["bad"],
        preferred_phrases=["good"],
        messaging_pillars=[{"pillar": "automation"}, {"pillar": "cloud"}],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── find_empty_calendar_slots ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_slots_all_free():
    # every execute → no scheduled post found
    db = _DB([_ResFirst(first=None)])
    slots = await A.find_empty_calendar_slots(db, uuid.uuid4(), days=2)
    assert len(slots) == 4  # 2 days x 2 slots
    assert all(s.hour in (9, 14) for s in slots)


@pytest.mark.asyncio
async def test_empty_slots_some_taken():
    db = _DB([_ResFirst(first=SimpleNamespace())])  # every slot taken
    slots = await A.find_empty_calendar_slots(db, uuid.uuid4(), days=1)
    assert slots == []


# ── generate_on_brand_content ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_content_brand_context(monkeypatch):
    import app.services.inference as I

    calls = {}
    monkeypatch.setattr(I, "call_inference", AsyncMock(return_value={"response": "  post text  "}))
    monkeypatch.setattr(A, "build_brand_system_prompt", lambda brand, voice: calls.update(brand=brand, voice=voice) or "CTX")
    out = await A.generate_on_brand_content(_DB([]), _brand(), _voice(), "automation", platform="x")
    assert out == "post text"
    assert calls["voice"]["banned_phrases"] == ["bad"]

    # no voice → voice=None in context; non-str response → str()
    calls.clear()
    import app.services.inference as I2

    monkeypatch.setattr(I2, "call_inference", AsyncMock(return_value={"response": 123}))
    out = await A.generate_on_brand_content(_DB([]), _brand(), None, "t")
    assert out == "123"
    assert calls["voice"] is None


# ── run_autopilot ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_autopilot_no_brand():
    db = _DB([_ResFirst(first=None)])
    out = await A.run_autopilot(db, uuid.uuid4(), uuid.uuid4())
    assert out["error"] == "No brand found"


@pytest.mark.asyncio
async def test_autopilot_no_pillars_or_values():
    brand = _brand(values=[])
    db = _DB([_ResFirst(first=brand), _ResFirst(first=None)])
    out = await A.run_autopilot(db, uuid.uuid4(), uuid.uuid4())
    assert "No messaging pillars" in out["error"]


@pytest.mark.asyncio
async def test_autopilot_values_fallback_no_slots(monkeypatch):
    brand = _brand(values=["speed", "honesty"])
    db = _DB([_ResFirst(first=brand), _ResFirst(first=None)])  # no voice
    monkeypatch.setattr(A, "find_empty_calendar_slots", AsyncMock(return_value=[]))
    out = await A.run_autopilot(db, uuid.uuid4(), uuid.uuid4())
    # values became pillars, but no free slots
    assert out.get("message", "").startswith("No empty") or out["created_count"] == 0


@pytest.mark.asyncio
async def test_autopilot_full_run(monkeypatch):
    brand = _brand()
    voice = _voice()
    db = _DB([_ResFirst(first=brand), _ResFirst(first=voice)])

    slots = [Mock(**{"isoformat.return_value": f"t{i}"}) for i in range(3)]
    monkeypatch.setattr(A, "find_empty_calendar_slots", AsyncMock(return_value=slots))
    gen = AsyncMock(side_effect=["good post", "low score post", RuntimeError("gen fail")])
    monkeypatch.setattr(A, "generate_on_brand_content", gen)
    scores = iter([{"score": 5}, {"score": 2}])
    monkeypatch.setattr(A, "score_brand_compliance", AsyncMock(side_effect=lambda **kw: next(scores)))

    out = await A.run_autopilot(db, uuid.uuid4(), uuid.uuid4())
    assert out["created_count"] == 1
    assert out["skipped_count"] == 2  # low score + exception
    assert out["drafts"][0]["topic"] == "automation"
    assert out["drafts"][0]["compliance_score"] == 5
