"""Unit tests for app/services/ai_functions.py (DMR-backed AI functions)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import ai_functions as af


def _settings(**over):
    base = dict(DMR_BASE_URL="", DMR_EMBED_URL="", DMR_MODEL="", DMR_EMBED_MODEL="", DMR_QUALITY_MODEL="")
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Client:
    """httpx.AsyncClient stand-in recording posts."""

    calls: list = []
    payload: dict = {}
    timeouts: list = []

    def __init__(self, timeout=None):
        _Client.timeouts.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _Client.calls.append((url, json))
        return _Resp(_Client.payload)


@pytest.fixture
def client(monkeypatch):
    _Client.calls, _Client.timeouts, _Client.payload = [], [], {}
    monkeypatch.setattr(af.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(af, "get_settings", lambda: _settings())
    return _Client


@pytest.fixture
def no_audit(monkeypatch):
    rec = []
    monkeypatch.setattr(af, "_audit", lambda *a, **k: rec.append(a))
    return rec


def test_cfg_defaults(monkeypatch):
    monkeypatch.setattr(af, "get_settings", lambda: _settings())
    base, embed, model, em = af._cfg()
    assert base.endswith("/chat/completions")
    assert embed.endswith("/embeddings")
    assert model == "ai/llama3.2"
    assert em.startswith("docker.io/ai/qwen3-embedding")


def test_cfg_overrides(monkeypatch):
    monkeypatch.setattr(af, "get_settings", lambda: _settings(DMR_BASE_URL=" http://x/c ", DMR_EMBED_URL="http://x/e", DMR_MODEL="m", DMR_EMBED_MODEL="em"))
    assert af._cfg() == ("http://x/c", "http://x/e", "m", "em")


@pytest.mark.asyncio
async def test_chat_default_and_quality(client, monkeypatch):
    client.payload = {"choices": [{"message": {"content": "  hi  "}}]}
    assert await af._chat("p", 5) == "hi"
    url, body = client.calls[-1]
    assert body["model"] == "ai/llama3.2" and body["max_tokens"] == 5
    assert client.timeouts[-1] == af._TIMEOUT

    assert await af._chat("p", quality=True) == "hi"
    assert client.calls[-1][1]["model"] == "docker.io/ai/qwen3:8b-q4_K_M"
    assert client.timeouts[-1] == af._TIMEOUT * 3

    monkeypatch.setattr(af, "get_settings", lambda: _settings(DMR_QUALITY_MODEL="big"))
    await af._chat("p", quality=True)
    assert client.calls[-1][1]["model"] == "big"


@pytest.mark.asyncio
async def test_chat_quality_blank_model_keeps_default(client, monkeypatch):
    client.payload = {"choices": [{"message": {"content": "x"}}]}
    # whitespace-only override strips to "" → default chat model kept
    monkeypatch.setattr(af, "get_settings", lambda: _settings(DMR_QUALITY_MODEL="   "))
    await af._chat("p", quality=True)
    assert client.calls[-1][1]["model"] == "ai/llama3.2"


@pytest.mark.asyncio
async def test_embed_truncates_input(client):
    client.payload = {"data": [{"embedding": [1.0, 2.0]}]}
    assert await af._embed("a" * 5000) == [1.0, 2.0]
    assert len(client.calls[-1][1]["input"]) == 1500


def test_cos():
    assert af._cos([1, 0], [1, 0]) == pytest.approx(1.0)
    assert af._cos([1, 0], [0, 1]) == pytest.approx(0.0)
    assert af._cos([0, 0], [0, 0]) == 0.0


@pytest.mark.asyncio
async def test_fix_grammar_ok_and_error(monkeypatch, no_audit):
    monkeypatch.setattr(af, "_chat", AsyncMock(return_value="Fixed."))
    assert await af.fix_grammar("fixd") == "Fixed."
    monkeypatch.setattr(af, "_chat", AsyncMock(side_effect=RuntimeError("down")))
    assert await af.fix_grammar("fixd") == "fixd"
    assert [a[1] for a in no_audit] == ["ok", "error"]


@pytest.mark.asyncio
async def test_ai_classify(monkeypatch, no_audit):
    monkeypatch.setattr(af, "_chat", AsyncMock(return_value="buy"))
    assert await af.ai_classify("buy, casual", "how much?") == "buy"
    monkeypatch.setattr(af, "_chat", AsyncMock(return_value="unknown"))
    assert await af.ai_classify(["buy", "casual"], "x") == "buy"
    monkeypatch.setattr(af, "_chat", AsyncMock(side_effect=RuntimeError))
    assert await af.ai_classify(["casual"], "x") == "casual"
    assert await af.ai_classify([], "x") == ""


@pytest.mark.asyncio
async def test_ai_extract(monkeypatch, no_audit):
    chat = AsyncMock(return_value="42")
    monkeypatch.setattr(af, "_chat", chat)
    assert await af.ai_extract("age", "I am 42", quality=True) == "42"
    assert chat.await_args.kwargs["quality"] is True
    monkeypatch.setattr(af, "_chat", AsyncMock(side_effect=RuntimeError))
    assert await af.ai_extract("age", "x") == "NONE"


@pytest.mark.asyncio
async def test_ai_mask(monkeypatch, no_audit):
    monkeypatch.setattr(af, "_chat", AsyncMock(return_value="[REDACTED-email]"))
    assert await af.ai_mask("a@b.c") == "[REDACTED-email]"
    monkeypatch.setattr(af, "_chat", AsyncMock(side_effect=RuntimeError))
    assert await af.ai_mask("a@b.c") == "a@b.c"


@pytest.mark.asyncio
async def test_ai_similarity(monkeypatch, no_audit):
    monkeypatch.setattr(af, "_embed", AsyncMock(side_effect=[[1.0, 0.0], [1.0, 0.0]]))
    assert await af.ai_similarity("a", "b") == 1.0
    monkeypatch.setattr(af, "_embed", AsyncMock(side_effect=RuntimeError))
    assert await af.ai_similarity("a", "b") == 0.0


@pytest.mark.asyncio
async def test_media_gate_ok(monkeypatch):
    monkeypatch.setattr(af, "fix_grammar", AsyncMock(return_value="clean"))
    monkeypatch.setattr(af, "ai_extract", AsyncMock(return_value="Score: 91"))
    monkeypatch.setattr(af, "ai_similarity", AsyncMock(return_value=0.2))
    out = await af.media_gate("raw", ["a", "b"])
    assert out == {"ok": True, "text": "clean", "score": 91, "similarity": 0.2, "reason": None}


@pytest.mark.asyncio
async def test_media_gate_low_score_no_digits(monkeypatch):
    monkeypatch.setattr(af, "fix_grammar", AsyncMock(return_value="clean"))
    monkeypatch.setattr(af, "ai_extract", AsyncMock(return_value="NONE"))
    out = await af.media_gate("raw")
    assert out["ok"] is False and out["score"] == 0 and out["reason"] == "nlp 0<80"


@pytest.mark.asyncio
async def test_media_gate_similar_breaks_early(monkeypatch):
    monkeypatch.setattr(af, "fix_grammar", AsyncMock(return_value="clean"))
    monkeypatch.setattr(af, "ai_extract", AsyncMock(return_value="95"))
    sim = AsyncMock(side_effect=[0.9, 0.1])
    monkeypatch.setattr(af, "ai_similarity", sim)
    out = await af.media_gate("raw", ["dup", "other"])
    assert out["ok"] is False and out["reason"] == "sim 0.9>=0.85"
    assert sim.await_count == 1


@pytest.mark.asyncio
async def test_media_gate_score_value_error(monkeypatch):
    """Unicode digits pass isdigit() but int() rejects some — falls back to 0."""
    monkeypatch.setattr(af, "fix_grammar", AsyncMock(return_value="clean"))
    monkeypatch.setattr(af, "ai_extract", AsyncMock(return_value="²"))
    out = await af.media_gate("raw")
    assert out["score"] == 0


def test_audit_without_loop_is_noop():
    af._audit("fn", "ok", 1.0, "text")  # no running loop → silently skipped


@pytest.mark.asyncio
async def test_audit_writes_row(monkeypatch):
    added = []

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    import sys
    import types

    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_maker", lambda: _Sess())
    monkeypatch.setattr(af, "get_settings", lambda: _settings())
    # NOTE: app.models.ai_function_call does not exist in the repo yet, so in
    # production this audit write always fails (swallowed at debug level).
    # Stand in a minimal model so the write path itself is exercised.
    fake = types.ModuleType("app.models.ai_function_call")
    fake.AIFunctionCall = lambda **kw: SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules, "app.models.ai_function_call", fake)
    af._audit("fix_grammar", "ok", 12.5, "secret text")
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(added) == 1
    row = added[0]
    assert row.fn == "fix_grammar" and row.outcome == "ok"
    assert row.input_sha != "secret text" and len(row.input_sha) == 32


@pytest.mark.asyncio
async def test_audit_write_failure_swallowed(monkeypatch):
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_maker", MagicMock(side_effect=RuntimeError("db")))
    af._audit("fn", "error", 1.0, "t")
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_timed_context_manager(monkeypatch):
    rec = []
    monkeypatch.setattr(af, "_audit", lambda *a: rec.append(a))
    async with af._timed("fn", "txt"):
        pass
    with pytest.raises(ValueError):
        async with af._timed("fn2", "txt"):
            raise ValueError("boom")
    assert [(r[0], r[1]) for r in rec] == [("fn", "ok"), ("fn2", "error")]
