"""Unit tests for app/services/skool_sync.py (browser-bridge Skool helpers)."""

from __future__ import annotations

import json

import httpx
import pytest

import app.services.skool_sync as S


class _Resp:
    def __init__(self, status: int, payload=None, bad_json: bool = False):
        self.status_code = status
        self._payload = payload
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient; records calls, replays responses."""

    calls: list = []
    responses: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, json=None, headers=None):
        _Client.calls.append((method, url, json, headers))
        return _Client.responses.pop(0)

    async def post(self, url, json=None):
        _Client.calls.append(("POST", url, json, None))
        r = _Client.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def client(monkeypatch):
    _Client.calls = []
    _Client.responses = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return _Client


@pytest.mark.asyncio
async def test_bridge_call_get_and_post(client):
    client.responses = [_Resp(200, {"a": 1}), _Resp(500, bad_json=True)]
    assert await S._bridge_call("/x") == (200, {"a": 1})
    assert await S._bridge_call("/y", {"b": 2}) == (500, {})
    assert client.calls[0][0] == "GET"
    assert client.calls[1][0] == "POST"
    assert client.calls[1][3] == {"X-Platform": "skool"}


@pytest.mark.asyncio
async def test_bridge_nav_and_eval(client):
    client.responses = [_Resp(200, {}), _Resp(200, {"result": "ok"}), _Resp(502, {})]
    await S._bridge_nav("https://example.com")
    assert client.calls[0][2] == {"url": "https://example.com"}
    assert await S._bridge_eval("1") == {"result": "ok"}
    assert await S._bridge_eval("2") is None


@pytest.mark.asyncio
async def test_trend_scan_parses_and_falls_back(monkeypatch):
    async def nav(url):
        return None

    results = iter([{"result": json.dumps([{"t": "hi", "eng": "3"}])}, None])

    async def ev(expr):
        return next(results)

    monkeypatch.setattr(S, "_bridge_nav", nav)
    monkeypatch.setattr(S, "_bridge_eval", ev)
    assert await S.trend_scan() == {"top": [{"t": "hi", "eng": "3"}]}
    assert await S.trend_scan() == {"top": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evals", "expected"),
    [
        (["no-composer"], {"ok": False, "error": "composer not found — session expired?"}),
        (["composer-open", "no-editor"], {"ok": False, "error": "editor not found"}),
        (["composer-open", "typed", "posted"], {"ok": True, "error": None}),
        (["composer-open", "typed", "no-submit"], {"ok": False, "error": "submit failed"}),
    ],
)
async def test_post_to_community(monkeypatch, evals, expected):
    seen: list = []

    async def nav(url):
        seen.append(url)

    it = iter(evals)

    async def ev(expr):
        return {"result": next(it)}

    monkeypatch.setattr(S, "_bridge_nav", nav)
    monkeypatch.setattr(S, "_bridge_eval", ev)
    assert await S.post_to_community("Title", "Body") == expected
    assert seen == [S.COMMUNITY]


@pytest.mark.asyncio
async def test_draft_community_post_ok_and_error(monkeypatch, client):
    async def scan():
        return {"top": [{"t": "post one"}, {"t": ""}, {}]}

    monkeypatch.setattr(S, "trend_scan", scan)
    monkeypatch.setenv("DMR_BASE_URL", "http://dmr.test")
    client.responses = [
        _Resp(200, {"choices": [{"message": {"content": "  Draft text  "}}]}),
        httpx.ConnectError("down"),
    ]
    out = await S.draft_community_post("visibility")
    assert out == {"draft": "Draft text", "trends_seen": 3}
    assert client.calls[0][1] == "http://dmr.test/engines/llamacpp/v1/chat/completions"
    assert "visibility" in client.calls[0][2]["messages"][0]["content"]
    assert await S.draft_community_post() == {"draft": "", "trends_seen": 3}
