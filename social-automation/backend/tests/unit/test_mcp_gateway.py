"""Unit tests for app.services.mcp_gateway + app.api.mcp_gateway."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import HTTPException

from app.api import mcp_gateway as gw_api
from app.services import mcp_gateway as gw


@pytest.fixture(autouse=True)
def _clear_caches():
    gw._tools_cache.clear()
    gw._session_cache.clear()
    yield
    gw._tools_cache.clear()
    gw._session_cache.clear()


def _server(**kw):
    base = {"name": "t", "url": "http://gw.test/mcp"}
    base.update(kw)
    return base


def _registry(monkeypatch, servers):
    monkeypatch.setattr(gw, "load_registry", lambda: {s["name"]: dict(s) for s in servers})


# ── registry / headers / parsing ──────────────────────────────────────


def test_load_registry_defaults_present():
    reg = gw.load_registry()
    assert {"linkedin", "airbyte", "nextcloud"} <= set(reg)
    assert all(s.get("enabled", True) for s in reg.values())


def test_load_registry_env_merge_and_bad_json(monkeypatch):
    monkeypatch.setenv(
        "MCP_GATEWAY_SERVERS",
        json.dumps(
            [
                {"name": "custom", "url": "http://c/mcp"},
                {"name": "linkedin", "url": "http://override/mcp"},
                {"no_name": True},
            ]
        ),
    )
    reg = gw.load_registry()
    assert reg["custom"]["url"] == "http://c/mcp"
    assert reg["linkedin"]["url"] == "http://override/mcp"
    assert "no_name" not in reg

    monkeypatch.setenv("MCP_GATEWAY_SERVERS", "{not json")
    reg2 = gw.load_registry()
    assert "linkedin" in reg2  # bad env ignored, defaults survive


def test_headers_extra_and_host():
    h = gw._headers(_server(headers={"Authorization": "Bearer x"}, host_header="localhost:1"))
    assert h["Content-Type"] == "application/json"
    assert h["Accept"] == "application/json, text/event-stream"
    assert h["Authorization"] == "Bearer x"
    assert h["Host"] == "localhost:1"
    assert "Host" not in gw._headers(_server())


def test_parse_jsonrpc_json_and_sse():
    r = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"a": 1}})
    assert gw._parse_jsonrpc(r)["result"] == {"a": 1}

    sse = httpx.Response(
        200,
        text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"b":2}}\n\n',
        headers={"content-type": "text/event-stream"},
    )
    assert gw._parse_jsonrpc(sse)["result"] == {"b": 2}

    bad = httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
    assert gw._parse_jsonrpc(bad) == {}


# ── _rpc session bootstrap ─────────────────────────────────────────────


def _mcp_handler(tools=None, session_expired_once=False):
    state = {"expired": False, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        body = json.loads(request.content)
        method = body.get("method")
        has_sid = "mcp-session-id" in request.headers
        if session_expired_once and has_sid and not state["expired"]:
            state["expired"] = True
            return httpx.Response(400, json={"error": "session expired"})
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "x"}},
                headers={"mcp-session-id": "sid-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {"tools": tools or []},
            },
        )

    return handler, state


@pytest.mark.asyncio
async def test_rpc_bootstraps_session_then_calls():
    handler, state = _mcp_handler(tools=[{"name": "t1"}])
    server = _server()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(server["url"]).mock(side_effect=handler)
        out = await gw._rpc(httpx.AsyncClient(), server, "tools/list", {}, 7)
    assert out["result"]["tools"] == [{"name": "t1"}]
    assert gw._session_cache["t"] == "sid-1"
    assert state["calls"] == 3  # initialize + initialized + tools/list
    assert route.called


@pytest.mark.asyncio
async def test_rpc_retries_once_on_expired_session():
    handler, state = _mcp_handler(tools=[{"name": "t2"}], session_expired_once=True)
    server = _server()
    gw._session_cache["t"] = "stale"
    with respx.mock(assert_all_called=False) as mock:
        mock.post(server["url"]).mock(side_effect=handler)
        out = await gw._rpc(httpx.AsyncClient(), server, "tools/list", {}, 3)
    assert out["result"]["tools"] == [{"name": "t2"}]
    assert gw._session_cache["t"] == "sid-1"  # re-initialized


# ── list_tools / call_tool / list_all ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_tools_unknown_and_disabled(monkeypatch):
    _registry(monkeypatch, [_server(name="off", enabled=False)])
    assert (await gw.list_tools("nope"))["error"].startswith("unknown server")
    assert (await gw.list_tools("off"))["error"] == "server disabled"


@pytest.mark.asyncio
async def test_list_tools_success_cache_and_error(monkeypatch):
    _registry(monkeypatch, [_server()])
    handler, _ = _mcp_handler(tools=[{"name": "search", "description": "d" * 400, "inputSchema": {"type": "object"}}])
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://gw.test/mcp").mock(side_effect=handler)
        out = await gw.list_tools("t")
        assert out["online"] is True
        assert out["tools"][0]["name"] == "search"
        assert len(out["tools"][0]["description"]) == 300  # truncated
        first_calls = route.call_count
        again = await gw.list_tools("t")
        assert again.get("cached") is True
        assert route.call_count == first_calls  # served from TTL cache

    # JSON-RPC error payload -> online False
    gw._tools_cache.clear()
    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://gw.test/mcp").mock(
            side_effect=lambda req: (
                httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": 0, "result": {}},
                    headers={"mcp-session-id": "s"},
                )
                if json.loads(req.content).get("method") == "initialize"
                else httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}})
            )
        )
        out = await gw.list_tools("t")
        assert out["online"] is False and "boom" in out["error"]

    # transport failure -> online False, no raise
    gw._tools_cache.clear()
    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://gw.test/mcp").mock(side_effect=httpx.ConnectError("refused"))
        out = await gw.list_tools("t")
        assert out["online"] is False and "refused" in out["error"]


@pytest.mark.asyncio
async def test_call_tool_paths(monkeypatch):
    _registry(monkeypatch, [_server()])
    assert (await gw.call_tool("nope", "x"))["error"].startswith("unknown server")

    gw._session_cache["t"] = "s"
    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://gw.test/mcp").mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"text": "hi"}]}},
            )
        )
        out = await gw.call_tool("t", "do_thing", {"a": 1})
        assert out["ok"] is True
        assert out["result"]["content"][0]["text"] == "hi"
        sent = json.loads(mock.calls.last.request.content)
        assert sent["method"] == "tools/call"
        assert sent["params"] == {"name": "do_thing", "arguments": {"a": 1}}

    gw._session_cache["t"] = "s"
    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://gw.test/mcp").mock(return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}}))
        out = await gw.call_tool("t", "bad")
        assert out["ok"] is False and out["error"]["message"] == "nope"

    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://gw.test/mcp").mock(side_effect=httpx.ReadTimeout("slow"))
        out = await gw.call_tool("t", "slow", timeout=1)
        assert out["ok"] is False and "slow" in out["error"]


@pytest.mark.asyncio
async def test_list_all_enabled_and_disabled(monkeypatch):
    _registry(
        monkeypatch,
        [_server(name="up"), _server(name="down", url="http://down/mcp", enabled=False)],
    )
    monkeypatch.setattr(
        gw,
        "list_tools",
        AsyncMock(return_value={"online": True, "tools": [{"name": "x"}]}),
    )
    out = await gw.list_all()
    assert out["up"]["online"] is True and out["up"]["tools"]
    assert out["down"]["enabled"] is False
    assert "online" not in out["down"]
    gw.list_tools.assert_awaited_once_with("up")


# ── API layer ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_list_servers(monkeypatch):
    monkeypatch.setattr(gw_api.mcp_gateway, "list_all", AsyncMock(return_value={"s": {"online": True}}))
    out = await gw_api.list_servers(SimpleNamespace())
    assert out["servers"] == {"s": {"online": True}}


@pytest.mark.asyncio
async def test_api_server_tools_404_and_ok(monkeypatch):
    monkeypatch.setattr(
        gw_api.mcp_gateway,
        "list_tools",
        AsyncMock(return_value={"online": False, "error": "unknown server 'ghost'"}),
    )
    with pytest.raises(HTTPException) as e:
        await gw_api.server_tools("ghost", SimpleNamespace())
    assert e.value.status_code == 404

    monkeypatch.setattr(
        gw_api.mcp_gateway,
        "list_tools",
        AsyncMock(return_value={"online": True, "tools": []}),
    )
    out = await gw_api.server_tools("real", SimpleNamespace())
    assert out["online"] is True


@pytest.mark.asyncio
async def test_api_call_tool_404_502_ok(monkeypatch):
    monkeypatch.setattr(gw_api.mcp_gateway, "load_registry", lambda: {"real": _server(name="real")})
    with pytest.raises(HTTPException) as e:
        await gw_api.call_server_tool("ghost", gw_api.CallBody(tool="t"), SimpleNamespace())
    assert e.value.status_code == 404

    monkeypatch.setattr(
        gw_api.mcp_gateway,
        "call_tool",
        AsyncMock(return_value={"ok": False, "error": "tool exploded"}),
    )
    with pytest.raises(HTTPException) as e:
        await gw_api.call_server_tool("real", gw_api.CallBody(tool="t"), SimpleNamespace())
    assert e.value.status_code == 502

    monkeypatch.setattr(
        gw_api.mcp_gateway,
        "call_tool",
        AsyncMock(return_value={"ok": True, "result": {"done": 1}}),
    )
    out = await gw_api.call_server_tool("real", gw_api.CallBody(tool="t", arguments={"x": 1}, timeout=5.0), SimpleNamespace())
    assert out["result"] == {"done": 1}
    gw_api.mcp_gateway.call_tool.assert_awaited_with("real", "t", {"x": 1}, timeout=5.0)
