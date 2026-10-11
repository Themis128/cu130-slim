"""Unit tests for brand_compliance system-prompt builder."""

import uuid
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.brand_compliance as bc
from app.services.brand_compliance import build_brand_system_prompt


class TestBuildBrandSystemPrompt:
    def test_empty_brand(self):
        prompt = build_brand_system_prompt({})
        assert "the brand" in prompt

    def test_voice_signature_small(self):
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"},
            {"voice_signature": {"niche": "We help SMBs run simple cloud"}},
        )
        assert "niche=We help SMBs" in prompt

    def test_voice_signature_capped(self):
        # The full VEC signature (~4K tokens) overflows the local models'
        # 2-4K context — every value must be truncated and the whole
        # signature section must stay under the budget.
        signature = {f"key{i}": {"text": "x" * 900} for i in range(20)}
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"}, {"voice_signature": signature}
        )
        sig_line = next(
            ln for ln in prompt.splitlines() if ln.startswith("Voice signature:")
        )
        assert len(sig_line) <= 3400
        assert "…" in sig_line
        # every key still present — truncation shrinks values, never drops keys
        for i in range(20):
            assert f"key{i}=" in sig_line

    def test_banned_phrases_still_rendered(self):
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"},
            {"banned_phrases": ["synergy"], "voice_signature": {"a": "b"}},
        )
        assert "synergy" in prompt
        assert "Voice signature:" in prompt

class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows, first=lambda: (self._rows[0] if self._rows else None))

class _DB:
    def __init__(self, results):
        self._q = deque(results)

    async def execute(self, q):
        return self._q.popleft()

@pytest.mark.asyncio
async def test_score_brand_compliance_banned_and_preferred(monkeypatch):
    monkeypatch.setattr(bc, "_ai_tone_match", AsyncMock(return_value={"score": 4, "suggestions": ["s"]}))
    out = await bc.score_brand_compliance(
        "We leverage synergy and clear skies for customers",
        {"name": "Cloudless"},
        {"banned_phrases": ["leverage", "synergy"],
         "preferred_phrases": ["clear skies", "customers"]},
        platform="linkedin",
    )
    assert out["banned_found"] == ["leverage", "synergy"]
    assert out["preferred_found"] == ["clear skies", "customers"]
    assert len(out["issues"]) == 2
    assert out["suggestions"] == ["s"]
    # 5 - 4 banned = 1, +1 preferred bonus = 2; blend 0.6*2 + 0.4*4 = 2.8 -> 3
    assert out["score"] == 3

@pytest.mark.asyncio
async def test_score_brand_compliance_no_voice(monkeypatch):
    monkeypatch.setattr(bc, "_ai_tone_match", AsyncMock(return_value=5))
    out = await bc.score_brand_compliance("clean content", {"name": "B"})
    assert out["banned_found"] == [] and out["issues"] == []
    # blend 0.6*5 + 0.4*5 = 5
    assert out["score"] == 5

@pytest.mark.parametrize("banned,preferred,tone,expected", [
    ([], [], 3, 4),                    # 5*0.6 + 3*0.4 = 4.2 -> 4
    (["x"], [], {"score": 1}, 2),      # (5-2)*0.6 + 1*0.4 = 2.2 -> 2
    ([], ["a", "b"], {"score": 5}, 5), # (5+1)*0.6 + 5*0.4 = 5.6 -> 5 (clamped... 6->5)
    ([], ["a"], "weird", 4),           # ai_score falls to 3: 5*0.6+3*0.4=4.2->4
])
def test_calculate_score(banned, preferred, tone, expected):
    assert bc._calculate_score(banned, preferred, tone, banned, preferred) == expected

@pytest.mark.asyncio
async def test_ai_tone_match_ok_and_fallback(monkeypatch):
    monkeypatch.setattr(bc, "call_inference",
        AsyncMock(return_value={"response": {"score": 5, "issues": [], "suggestions": ["x"]}}))
    out = await bc._ai_tone_match(
        "content",
        {"name": "Cloudless", "positioning_statement": "pos"},
        {"tone_dimensions": {"formal": 3}, "example_content": "ex",
         "messaging_pillars": ["p"]},
        "linkedin",
    )
    assert out["score"] == 5

    # response key absent -> result itself
    monkeypatch.setattr(bc, "call_inference", AsyncMock(return_value={"score": 2}))
    out = await bc._ai_tone_match("c", {}, {}, None)
    assert out["score"] == 2

    # exception -> neutral fallback
    monkeypatch.setattr(bc, "call_inference", AsyncMock(side_effect=RuntimeError("down")))
    out = await bc._ai_tone_match("c", {}, {}, None)
    assert out == {"score": 3, "issues": [], "suggestions": []}

@pytest.mark.asyncio
async def test_load_brand_context():
    # no team_id
    assert await bc.load_brand_context(_DB([]), None) == (None, None, "")

    # no brand row
    db = _DB([_Res([])])
    assert await bc.load_brand_context(db, uuid.uuid4()) == (None, None, "")

    voice = SimpleNamespace(
        tone_dimensions={"formal": 4}, messaging_pillars=[{"pillar": "p"}],
        banned_phrases=["bad"], preferred_phrases=["good"],
        example_content="ex", voice_signature={"k": "v"},
    )
    brand = SimpleNamespace(
        name="Cloudless", positioning_statement="pos", mission="m",
        values=["v1"], tagline="t", target_audience={"who": "founders"},
        voice=voice,
    )
    db2 = _DB([_Res([brand])])
    b, v, prompt = await bc.load_brand_context(db2, uuid.uuid4())
    assert b["name"] == "Cloudless"
    assert v["banned_phrases"] == ["bad"]
    assert "You are writing content for Cloudless" in prompt
    assert "NEVER use these phrases: bad" in prompt

    # brand without voice
    brand2 = SimpleNamespace(
        name="B", positioning_statement=None, mission=None,
        values=None, tagline=None, target_audience=None, voice=None,
    )
    db3 = _DB([_Res([brand2])])
    b2, v2, prompt2 = await bc.load_brand_context(db3, uuid.uuid4())
    assert v2 is None and "content for B" in prompt2

def test_build_brand_system_prompt_sections():
    brand = {
        "name": "Cloudless", "positioning_statement": "pos",
        "mission": "m", "values": ["a", "b"], "tagline": "tag",
        "target_audience": {"who": "founders"},
    }
    voice = {
        "tone_dimensions": {"formal": 4, "witty": 2},
        "messaging_pillars": [{"pillar": "Speed"}, {"title": "Trust"}],
        "banned_phrases": ["bad"], "preferred_phrases": ["good"],
        "example_content": "on-brand example",
        "voice_signature": {"tone": "confident", "meta": {"a": 1}},
    }
    out = bc.build_brand_system_prompt(brand, voice)
    assert "tagline: tag" in out
    assert "positioning: pos" in out
    assert "mission: m" in out
    assert "values: a, b" in out
    assert "formal: 4/5" in out
    assert "Voice signature: tone=confident" in out
    assert "Speed, Trust" in out
    assert "NEVER use these phrases: bad" in out
    assert "Prefer these phrases: good" in out
    assert "on-brand example" in out

def test_build_brand_system_prompt_minimal_and_signature_budget():
    out = bc.build_brand_system_prompt({"name": "B"})
    assert "content for B" in out and "Write content" in out
    assert "tagline" not in out

    # signature larger than budget -> caps shrink until it fits
    big = {"k" + str(i): "x" * 500 for i in range(10)}
    out2 = bc.build_brand_system_prompt(
        {"name": "B"}, {"voice_signature": big})
    assert "Voice signature:" in out2
