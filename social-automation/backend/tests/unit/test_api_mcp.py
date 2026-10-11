"""Coverage for app/api/mcp.py — MCP stack status endpoints."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.mcp as MCP


def _user():
    return SimpleNamespace(id=uuid.uuid4())


class _Resp:
    def __init__(self, status=200, body=None, text="", headers=None,
                 content=b"png"):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}
        self.content = content

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _HTTP:
    def __init__(self):
        self.gets = []
        self.posts = []
        self.get_map = {}
        self.post_queue = []
        self.exc = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        self.gets.append(url)
        if self.exc:
            raise self.exc
        r = self.get_map.get(url)
        if isinstance(r, BaseException):
            raise r
        return r or _Resp()

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        if self.exc:
            raise self.exc
        item = self.post_queue.pop(0) if self.post_queue else _Resp()
        if isinstance(item, BaseException):
            raise item
        return item


def _wire(monkeypatch, client):
    monkeypatch.setattr(MCP.httpx, "AsyncClient", lambda **kw: client)


SSE_TOOLS = (
    'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":'
    '[{"name":"get_profile","description":"read profile"},'
    '{"name":"search","description":null}]}}\n'
)


class TestCheckHttp:
    @pytest.mark.asyncio
    async def test_ok_json(self, monkeypatch):
        c = _HTTP()
        c.get_map["http://x/health"] = _Resp(200, {"up": True})
        _wire(monkeypatch, c)
        out = await MCP._check_http("http://x/health")
        assert out == {"online": True, "status_code": 200,
                       "data": {"up": True}}

    @pytest.mark.asyncio
    async def test_non_json_body(self, monkeypatch):
        c = _HTTP()
        c.get_map["http://x"] = _Resp(502, body=None, text="bad gateway")
        _wire(monkeypatch, c)
        out = await MCP._check_http("http://x")
        assert out["online"] is False
        assert out["data"]["text"] == "bad gateway"

    @pytest.mark.asyncio
    async def test_unreachable(self, monkeypatch):
        _wire(monkeypatch, _HTTP())
        monkeypatch.setattr(MCP.httpx, "AsyncClient",
                            lambda **kw: _HTTP())
        c = _HTTP()
        c.exc = TimeoutError()
        _wire(monkeypatch, c)
        out = await MCP._check_http("http://x")
        assert out == {"online": False, "error": "unreachable"}


class TestParseSse:
    def test_parses_data_line(self):
        out = MCP._parse_sse_response('event: x\ndata: {"a":1}\n')
        assert out == {"a": 1}

    def test_bad_json_and_no_data(self):
        assert MCP._parse_sse_response('data: {bad\n') == {}
        assert MCP._parse_sse_response('event: y\n') == {}


class TestMcpInitAndListTools:
    @pytest.mark.asyncio
    async def test_happy_with_session(self, monkeypatch):
        c = _HTTP()
        c.post_queue = [
            _Resp(200, headers={"mcp-session-id": "abc123def456"}),
            _Resp(202),
            _Resp(200, text=SSE_TOOLS),
        ]
        _wire(monkeypatch, c)
        out = await MCP._mcp_initialize_and_list_tools(
            "http://mcp:9227/mcp")
        assert out["online"] is True
        assert out["session_id"] == "abc123def456..."
        assert len(out["tools"]) == 2
        assert out["tools"][0]["name"] == "get_profile"
        assert out["tools"][1]["description"] == ""
        # session header sent on subsequent calls
        assert c.posts[1][1]["headers"]["Mcp-Session-Id"] == "abc123def456"

    @pytest.mark.asyncio
    async def test_init_non200(self, monkeypatch):
        c = _HTTP()
        c.post_queue = [_Resp(500, text="oops")]
        _wire(monkeypatch, c)
        out = await MCP._mcp_initialize_and_list_tools("http://m/mcp")
        assert out["online"] is False
        assert "HTTP 500" in out["error"]

    @pytest.mark.asyncio
    async def test_tools_non200_warns(self, monkeypatch):
        c = _HTTP()
        c.post_queue = [_Resp(200), _Resp(202), _Resp(404)]
        _wire(monkeypatch, c)
        out = await MCP._mcp_initialize_and_list_tools("http://m/mcp")
        assert out["online"] is True
        assert out["tools"] == []
        assert "404" in out["warning"]
        assert out["session_id"] == "none"

    @pytest.mark.asyncio
    async def test_no_port_no_session(self, monkeypatch):
        c = _HTTP()
        c.post_queue = [_Resp(200), _Resp(202),
                        _Resp(200, text=SSE_TOOLS)]
        _wire(monkeypatch, c)
        out = await MCP._mcp_initialize_and_list_tools("http://host/mcp")
        assert out["session_id"] == "none"
        assert out["online"] is True

    @pytest.mark.asyncio
    async def test_unreachable(self, monkeypatch):
        c = _HTTP()
        c.exc = OSError()
        _wire(monkeypatch, c)
        out = await MCP._mcp_initialize_and_list_tools("http://m/mcp")
        assert out == {"online": False, "error": "unreachable"}


class TestStackStatus:
    @pytest.mark.asyncio
    async def test_aggregates(self, monkeypatch):
        monkeypatch.setattr(MCP, "_check_http", AsyncMock(
            return_value={"online": True, "status_code": 200}))
        monkeypatch.setattr(MCP, "_mcp_initialize_and_list_tools", AsyncMock(
            side_effect=[{"online": True, "tools": []},
                         RuntimeError("airbyte gone")]))
        out = await MCP.get_mcp_stack_status(current_user=_user())
        assert out["status"] == "ok"
        assert out["total_services"] == 5
        ids = {s["id"] for s in out["services"]}
        assert ids == {"linkedin_sidecar", "facebook_sidecar",
                       "instagram_sidecar", "linkedin_mcp", "airbyte_mcp"}
        air = next(s for s in out["services"] if s["id"] == "airbyte_mcp")
        assert air["online"] is False
        assert "airbyte gone" in air["error"]
        assert out["online_services"] == 4


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_unknown_service(self):
        with pytest.raises(HTTPException) as ei:
            await MCP.get_screenshot("nope", current_user=_user())
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        c = _HTTP()
        c.get_map["http://linkedin-browser-sidecar:9225/screenshot"] = \
            _Resp(200, content=b"\x89PNG")
        _wire(monkeypatch, c)
        out = await MCP.get_screenshot("linkedin_sidecar",
                                       current_user=_user())
        assert out.body == b"\x89PNG"
        assert out.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_sidecar_error(self, monkeypatch):
        c = _HTTP()
        c.get_map["http://facebook-browser-sidecar:9226/screenshot"] = \
            _Resp(503)
        _wire(monkeypatch, c)
        with pytest.raises(HTTPException) as ei:
            await MCP.get_screenshot("facebook_sidecar",
                                     current_user=_user())
        # the raised 503 is re-wrapped by the broad except → 500
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unreachable(self, monkeypatch):
        c = _HTTP()
        c.exc = TimeoutError()
        _wire(monkeypatch, c)
        with pytest.raises(HTTPException) as ei:
            await MCP.get_screenshot("linkedin_sidecar",
                                     current_user=_user())
        assert ei.value.status_code == 500


class TestCheckSession:
    @pytest.mark.asyncio
    async def test_unknown(self):
        with pytest.raises(HTTPException) as ei:
            await MCP.check_session("bad", current_user=_user())
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_ok_and_error(self, monkeypatch):
        c = _HTTP()
        c.get_map["http://linkedin-browser-sidecar:9225/session"] = \
            _Resp(200, {"logged_in": True})
        _wire(monkeypatch, c)
        out = await MCP.check_session("linkedin_sidecar",
                                      current_user=_user())
        assert out == {"status": "ok", "result": {"logged_in": True}}
        c.exc = RuntimeError()
        out = await MCP.check_session("linkedin_sidecar",
                                      current_user=_user())
        assert out["status"] == "error"


class TestLinkedinMcpCalls:
    def _client_with(self, text_payload):
        c = _HTTP()
        c.post_queue = [
            _Resp(200, headers={"mcp-session-id": "s1"}),
            _Resp(202),
            _Resp(200, text=text_payload),
        ]
        return c

    @pytest.mark.asyncio
    async def test_get_profile(self, monkeypatch):
        sse = ('data: {"result":{"content":[{"type":"text",'
               '"text":"profile-data"}]}}\n')
        c = self._client_with(sse)
        _wire(monkeypatch, c)
        out = await MCP.linkedin_mcp_get_profile(current_user=_user())
        assert out == {"status": "ok", "profile": "profile-data"}
        # tool call was get_my_profile
        assert c.posts[2][1]["json"]["params"]["name"] == "get_my_profile"

    @pytest.mark.asyncio
    async def test_get_profile_error(self, monkeypatch):
        c = _HTTP()
        c.exc = TimeoutError()
        _wire(monkeypatch, c)
        out = await MCP.linkedin_mcp_get_profile(current_user=_user())
        assert out["status"] == "error"

    @pytest.mark.asyncio
    async def test_search_people(self, monkeypatch):
        sse = ('data: {"result":{"content":[{"type":"image","data":"x"},'
               '{"type":"text","text":"people"}]}}\n')
        c = self._client_with(sse)
        _wire(monkeypatch, c)
        out = await MCP.linkedin_mcp_search_people(
            keywords="devops", current_user=_user())
        assert out == {"status": "ok", "results": "people"}
        assert c.posts[2][1]["json"]["params"]["arguments"] == {
            "keywords": "devops"}

    @pytest.mark.asyncio
    async def test_search_people_error(self, monkeypatch):
        c = _HTTP()
        c.exc = OSError()
        _wire(monkeypatch, c)
        out = await MCP.linkedin_mcp_search_people(
            keywords="x", current_user=_user())
        assert out["status"] == "error"
