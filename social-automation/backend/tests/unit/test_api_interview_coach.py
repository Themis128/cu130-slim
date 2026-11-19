"""Unit tests for app/api/interview_coach.py — worker proxy router."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

import app.api.interview_coach as IC
from app.models.user import UserRole

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
    body = IC.ChatIn(messages=[{"role": "user", "content": "hi"}], stream=False)
    await IC.chat(body, USER)
    assert seen["method"] == "POST"
    assert seen["path"] == "/engines/chat/completions"
    # None fields are stripped before forwarding
    assert "model" not in seen["kw"]["json_body"]
    assert seen["kw"]["json_body"]["stream"] is False


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
    assert out["error"] == "upstream unreachable"


# ── /stt websocket auth + upstream url ────────────────────────────────


@pytest.mark.asyncio
async def test_ws_authenticate_rejects_missing_token():
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as ei:
        await IC._ws_authenticate(None, db)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_ws_authenticate_rejects_non_access_token(monkeypatch):
    monkeypatch.setattr(IC, "decode_token", lambda t: {"type": "refresh"})
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as ei:
        await IC._ws_authenticate("tok", db)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_ws_authenticate_rejects_unknown_user(monkeypatch):
    uid = uuid.uuid4()
    monkeypatch.setattr(
        IC, "decode_token", lambda t: {"type": "access", "sub": str(uid)}
    )
    res = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    with pytest.raises(HTTPException) as ei:
        await IC._ws_authenticate("tok", db)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_ws_authenticate_returns_user(monkeypatch):
    uid = uuid.uuid4()
    monkeypatch.setattr(
        IC, "decode_token", lambda t: {"type": "access", "sub": str(uid)}
    )
    monkeypatch.setattr(
        IC, "get_user_role", AsyncMock(return_value=UserRole.ADMIN)
    )
    user = SimpleNamespace(id=uid)
    res = SimpleNamespace(scalar_one_or_none=lambda: user)
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    assert await IC._ws_authenticate("tok", db) is user


@pytest.mark.asyncio
async def test_ws_authenticate_rejects_non_admin(monkeypatch):
    uid = uuid.uuid4()
    monkeypatch.setattr(
        IC, "decode_token", lambda t: {"type": "access", "sub": str(uid)}
    )
    monkeypatch.setattr(
        IC, "get_user_role", AsyncMock(return_value=UserRole.VIEWER)
    )
    user = SimpleNamespace(id=uid)
    res = SimpleNamespace(scalar_one_or_none=lambda: user)
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    with pytest.raises(HTTPException) as ei:
        await IC._ws_authenticate("tok", db)
    assert ei.value.status_code == 403


def test_ws_upstream_url_translates_scheme_and_ch():
    url = IC._ws_upstream_url("tab-1")
    assert url.startswith("wss://ic.example.workers.dev/stt?ch=tab-1")


def test_stt_ws_channel_sanitized():
    # ch filter: alnum + -_ only, capped at 64 chars
    raw = "a b<script>" + "x" * 100
    ch = "".join(c for c in raw if c.isalnum() or c in "-_")[:64] or "tab"
    assert "<" not in ch and " " not in ch and len(ch) <= 64


# ── /stt websocket pump + endpoint ────────────────────────────────────


class _FakeClientWS:
    def __init__(self, query_params=None, incoming=()):
        self.query_params = query_params or {}
        self._incoming = list(incoming)
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed = code

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect"}

    async def send_text(self, data):
        self.sent.append(("text", data))

    async def send_bytes(self, data):
        self.sent.append(("bytes", data))


class _FakeUpstream:
    def __init__(self, messages=()):
        self._messages = list(messages)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _ws_db(user=None):
    res = SimpleNamespace(scalar_one_or_none=lambda: user)
    return SimpleNamespace(execute=AsyncMock(return_value=res))


def _auth_ok(monkeypatch, role=UserRole.ADMIN):
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid)
    monkeypatch.setattr(
        IC, "decode_token", lambda t: {"type": "access", "sub": str(uid)}
    )
    monkeypatch.setattr(IC, "get_user_role", AsyncMock(return_value=role))
    return user, _ws_db(user)


@pytest.mark.asyncio
async def test_stt_ws_rejects_bad_token():
    ws = _FakeClientWS(query_params={})
    await IC.stt_ws(ws, db=_ws_db())
    assert ws.closed == 4401
    assert not ws.accepted


@pytest.mark.asyncio
async def test_stt_ws_unconfigured_closes_4503(monkeypatch):
    _, db = _auth_ok(monkeypatch)
    monkeypatch.setattr(IC, "_config", lambda: ("", ""))
    ws = _FakeClientWS(query_params={"token": "t"})
    await IC.stt_ws(ws, db=db)
    assert ws.closed == 4503
    assert not ws.accepted


@pytest.mark.asyncio
async def test_stt_ws_upstream_failure_closes_1011(monkeypatch):
    user, db = _auth_ok(monkeypatch)
    monkeypatch.setattr(IC, "_config", lambda: ("https://ic.example", "k"))

    class _Boom:
        async def __aenter__(self):
            raise OSError("connect refused")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(IC.websockets, "connect", lambda *a, **k: _Boom())
    ws = _FakeClientWS(query_params={"token": "t", "ch": "tab"})
    await IC.stt_ws(ws, db=db)
    assert ws.closed == 1011
    assert not ws.accepted


@pytest.mark.asyncio
async def test_stt_ws_pumps_text_and_binary(monkeypatch):
    user, db = _auth_ok(monkeypatch)
    monkeypatch.setattr(IC, "_config", lambda: ("https://ic.example", "k"))
    upstream = _FakeUpstream(messages=[b"\x00\x01audio", "transcript"])
    monkeypatch.setattr(
        IC.websockets, "connect", lambda *a, **k: upstream
    )
    ws = _FakeClientWS(
        query_params={"token": "t"},
        incoming=[{"type": "websocket.receive", "bytes": b"mic-frame"}],
    )
    await IC.stt_ws(ws, db=db)
    assert ws.accepted
    assert b"mic-frame" in upstream.sent
    kinds = {k for k, _ in ws.sent}
    assert kinds <= {"text", "bytes"}
    assert ("bytes", b"\x00\x01audio") in ws.sent
    assert ("text", "transcript") in ws.sent


@pytest.mark.asyncio
async def test_pump_stt_client_disconnect_cancels_upstream():
    upstream = _FakeUpstream(messages=[])
    ws = _FakeClientWS(incoming=[{"type": "websocket.disconnect"}])
    await IC._pump_stt(ws, upstream)
    assert ws.sent == []


# ── streaming proxy + admin gate ──────────────────────────────────────


class _FakeStreamResp:
    def __init__(self, status=200, chunks=(), headers=None):
        self.status_code = status
        self.headers = headers or {"content-type": "text/event-stream"}
        self._chunks = list(chunks)
        self.closed = False

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aread(self):
        return b'{"error": "boom"}'

    async def aclose(self):
        self.closed = True


class _FakeStreamClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.closed = False
        self.sent = None

    def build_request(self, method, path, **kw):
        self.sent = (method, path, kw)
        return object()

    async def send(self, req, stream=False):
        if self._exc is not None:
            raise self._exc
        return self._resp

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_proxy_stream_yields_chunks_and_closes(monkeypatch):
    resp = _FakeStreamResp(chunks=[b"data: one\n\n", b"data: two\n\n"])
    client = _FakeStreamClient(resp=resp)
    monkeypatch.setattr(IC, "_raw_client", lambda timeout: client)
    out = await IC._proxy_stream(
        "POST", "/engines/chat/completions", json_body={"stream": True}
    )
    assert isinstance(out, StreamingResponse)
    assert out.media_type == "text/event-stream"
    chunks = [c async for c in out.body_iterator]
    assert chunks == [b"data: one\n\n", b"data: two\n\n"]
    assert resp.closed and client.closed


@pytest.mark.asyncio
async def test_proxy_stream_error_status_returns_json(monkeypatch):
    resp = _FakeStreamResp(status=500)
    client = _FakeStreamClient(resp=resp)
    monkeypatch.setattr(IC, "_raw_client", lambda timeout: client)
    out = await IC._proxy_stream("GET", "/latest")
    assert out.status_code == 500
    assert resp.closed and client.closed


@pytest.mark.asyncio
async def test_proxy_stream_connect_error_502(monkeypatch):
    client = _FakeStreamClient(exc=httpx.ConnectError("down"))
    monkeypatch.setattr(IC, "_raw_client", lambda timeout: client)
    with pytest.raises(HTTPException) as ei:
        await IC._proxy_stream("GET", "/latest")
    assert ei.value.status_code == 502
    assert client.closed


@pytest.mark.asyncio
async def test_chat_stream_uses_streaming_proxy(monkeypatch):
    sentinel = object()
    mock_stream = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(IC, "_proxy_stream", mock_stream)
    body = IC.ChatIn(messages=[{"role": "user", "content": "hi"}], stream=True)
    out = await IC.chat(body, USER)
    assert out is sentinel
    mock_stream.assert_awaited_once()
    assert mock_stream.await_args.args[1] == "/engines/chat/completions"


@pytest.mark.asyncio
async def test_chat_non_stream_uses_buffered_proxy(monkeypatch):
    sentinel = object()
    mock_proxy = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(IC, "_proxy", mock_proxy)
    body = IC.ChatIn(messages=[{"role": "user", "content": "hi"}], stream=False)
    out = await IC.chat(body, USER)
    assert out is sentinel
    mock_proxy.assert_awaited_once()
