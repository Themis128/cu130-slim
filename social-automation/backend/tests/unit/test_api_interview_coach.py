"""Unit tests for app/api/interview_coach.py — worker proxy router."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import app.api.interview_coach as IC

USER = SimpleNamespace(id="u1")


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(
        IC, "_config", lambda: ("https://ic.example.workers.dev", "sekret")
    )


# ── config gating ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_raises_503_when_unconfigured(monkeypatch):
    monkeypatch.setattr(IC, "_config", lambda: ("", ""))
    with pytest.raises(HTTPException) as ei:
        IC._client(5.0)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_client_partial_config_also_503(monkeypatch):
    monkeypatch.setattr(IC, "_config", lambda: ("https://x.dev", ""))
    with pytest.raises(HTTPException) as ei:
        IC._client(5.0)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_client_sets_base_url_and_xkey(monkeypatch):
    monkeypatch.setattr(
        IC, "_config", lambda: ("https://ic.example.workers.dev", "sekret")
    )
    captured = {}

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(IC.httpx, "AsyncClient", _FakeClient)
    IC._client(7.0)
    assert captured["base_url"] == "https://ic.example.workers.dev"
    assert captured["headers"] == {"x-key": "sekret"}
    assert captured["timeout"] == 7.0


# ── _proxy error mapping ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proxy_maps_httpx_error_to_502(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(IC.httpx, "AsyncClient", lambda **kw: _Boom())
    with pytest.raises(HTTPException) as ei:
        await IC._proxy("GET", "/latest")
    assert ei.value.status_code == 502
    assert "upstream error" in ei.value.detail


@pytest.mark.asyncio
async def test_proxy_passes_json_and_status(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b"{}"
        text = "{}"

        def json(self):
            return {"who": "interviewer"}

    class _Ok:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, path, **kw):
            self.kw = kw
            return _Resp()

    client = _Ok()
    monkeypatch.setattr(IC.httpx, "AsyncClient", lambda **kw: client)
    resp = await IC._proxy("POST", "/classify", json_body={"text": "hi?"})
    assert resp.status_code == 200
    assert b'"who"' in resp.body
    assert client.kw["json"] == {"text": "hi?"}


@pytest.mark.asyncio
async def test_proxy_audio_passthrough_media_type(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "audio/mpeg"}
        content = b"ID3fake"
        text = ""

    class _Ok:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(IC.httpx, "AsyncClient", lambda **kw: _Ok())
    resp = await IC._proxy("POST", "/tts", json_body={"text": "hi"})
    assert resp.media_type == "audio/mpeg"
    assert resp.body == b"ID3fake"


# ── endpoint routing ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_maps_to_engines_completions(monkeypatch):
    seen = {}

    async def fake_proxy(method, path, **kw):
        seen.update(method=method, path=path, kw=kw)
        return IC.Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(IC, "_proxy", fake_proxy)
    body = IC.ChatIn(messages=[{"role": "user", "content": "hi"}], stream=True)
    await IC.chat(body, USER)
    assert seen["method"] == "POST"
    assert seen["path"] == "/engines/chat/completions"
    # None fields are stripped before forwarding
    assert "model" not in seen["kw"]["json_body"]
    assert seen["kw"]["json_body"]["stream"] is True


@pytest.mark.asyncio
async def test_seed_forwards_texts(monkeypatch):
    seen = {}

    async def fake_proxy(method, path, **kw):
        seen.update(path=path, kw=kw)
        return IC.Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(IC, "_proxy", fake_proxy)
    await IC.seed(IC.SeedIn(texts=["fact one"]), USER)
    assert seen["path"] == "/seed"
    assert seen["kw"]["json_body"] == {"texts": ["fact one"]}


@pytest.mark.asyncio
async def test_log_forwards_session_who_text(monkeypatch):
    seen = {}

    async def fake_proxy(method, path, **kw):
        seen.update(path=path, kw=kw)
        return IC.Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(IC, "_proxy", fake_proxy)
    await IC.log_turn(
        IC.LogIn(session="s1", who="candidate", text="answer"), USER
    )
    assert seen["path"] == "/log"
    assert seen["kw"]["json_body"]["who"] == "candidate"


@pytest.mark.asyncio
async def test_latest_and_history_paths(monkeypatch):
    paths = []

    async def fake_proxy(method, path, **kw):
        paths.append((method, path))
        return IC.Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(IC, "_proxy", fake_proxy)
    await IC.latest(USER)
    await IC.history(USER)
    assert paths == [("GET", "/latest"), ("GET", "/history")]


# ── transcribe guards ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_body():
    req = SimpleNamespace(body=AsyncMock(return_value=b""))
    with pytest.raises(HTTPException) as ei:
        await IC.transcribe(req, USER)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_transcribe_rejects_oversize():
    req = SimpleNamespace(body=AsyncMock(return_value=b"x" * (26 * 1024 * 1024)))
    with pytest.raises(HTTPException) as ei:
        await IC.transcribe(req, USER)
    assert ei.value.status_code == 413


@pytest.mark.asyncio
async def test_transcribe_forwards_octet_stream(monkeypatch):
    seen = {}

    async def fake_proxy(method, path, **kw):
        seen.update(method=method, path=path, kw=kw)
        return IC.Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(IC, "_proxy", fake_proxy)
    req = SimpleNamespace(body=AsyncMock(return_value=b"\x00\x01wav"))
    await IC.transcribe(req, USER)
    assert seen["path"] == "/transcribe"
    assert seen["kw"]["content"] == b"\x00\x01wav"
    assert seen["kw"]["content_type"] == "application/octet-stream"


# ── status endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_unconfigured():
    import app.api.interview_coach as m

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(m, "_config", lambda: ("", ""))
    out = await m.status(USER)
    assert out == {"configured": False}
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_status_upstream_error(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(IC.httpx, "AsyncClient", lambda **kw: _Boom())
    out = await IC.status(USER)
    assert out["configured"] is True
    assert out["upstream_status"] is None
    assert "down" in out["error"]
