"""Coverage for app/services/kv_client.py."""

from unittest.mock import AsyncMock

import pytest

import app.services.kv_client as K


class _Resp:
    def __init__(self, status=200, text="", data=None):
        self.status_code = status
        self.text = text
        self._data = data

    def json(self):
        return self._data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _HTTP:
    def __init__(self, handler):
        self.handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return self.handler("GET", url, **kw)

    async def put(self, url, **kw):
        return self.handler("PUT", url, **kw)

    async def delete(self, url, **kw):
        return self.handler("DELETE", url, **kw)


def _client(**kw):
    c = K.KVClient()
    c.account_id = kw.get("account_id", "acc")
    c.cache_ns = kw.get("cache_ns", "ns")
    c._tokens = kw.get("tokens", ["t1", "t2"])
    c.api_token = c._tokens[0] if c._tokens else ""
    c._enabled = kw.get("enabled")
    c._active_token = None
    return c


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(K.httpx, "AsyncClient", lambda **kw: _HTTP(handler))


class TestInitProps:
    def test_enabled(self):
        assert _client(enabled=None).enabled is True
        assert _client(enabled=None, tokens=[]).enabled is False
        c = _client(enabled=None)
        c.account_id = ""
        assert c.enabled is False

    def test_base_url_lazy(self):
        c = _client()
        url = c.base_url
        assert "acc" in url and "ns" in url
        assert c.base_url is url  # cached

    def test_headers(self):
        c = _client()
        assert c._headers()["Authorization"] == "Bearer t1"
        c._active_token = "tX"
        assert c._headers()["Authorization"] == "Bearer tX"


class TestTryTokens:
    @pytest.mark.asyncio
    async def test_first_ok(self):
        c = _client()
        method = AsyncMock(return_value=_Resp(200))
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 200
        assert c._active_token == "t1"

    @pytest.mark.asyncio
    async def test_401_then_ok(self):
        c = _client()
        method = AsyncMock(side_effect=[_Resp(401), _Resp(200)])
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 200
        assert method.await_count == 2

    @pytest.mark.asyncio
    async def test_all_401_returns_last(self):
        c = _client()
        method = AsyncMock(return_value=_Resp(401))
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 401
        assert c._active_token is None

    @pytest.mark.asyncio
    async def test_all_raise_runtime(self):
        c = _client()
        method = AsyncMock(side_effect=RuntimeError("net"))
        with pytest.raises(RuntimeError, match="All Cloudflare"):
            await c._try_tokens(method, "u")

    @pytest.mark.asyncio
    async def test_raise_then_ok(self):
        c = _client()
        method = AsyncMock(side_effect=[RuntimeError("net"), _Resp(200)])
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 200


class TestGet:
    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await _client(enabled=False).get("k") is None

    @pytest.mark.asyncio
    async def test_404_none(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(404))
        assert await _client().get("k") is None

    @pytest.mark.asyncio
    async def test_text(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, "v"))
        assert await _client().get("k") == "v"

    @pytest.mark.asyncio
    async def test_500_raises(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(500))
        with pytest.raises(RuntimeError):
            await _client().get("k")


class TestGetJson:
    @pytest.mark.asyncio
    async def test_none(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(404))
        assert await _client().get_json("k") is None

    @pytest.mark.asyncio
    async def test_valid(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, '{"a":1}'))
        assert await _client().get_json("k") == {"a": 1}

    @pytest.mark.asyncio
    async def test_invalid_returns_raw(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, "nope{"))
        assert await _client().get_json("k") == "nope{"


class TestPutDelete:
    @pytest.mark.asyncio
    async def test_put_disabled(self):
        assert await _client(enabled=False).put("k", "v") is False

    @pytest.mark.asyncio
    async def test_put_ok_and_ttl(self, monkeypatch):
        seen = {}

        def handler(m, u, **kw):
            seen.update(kw)
            return _Resp(200)

        _patch_http(monkeypatch, handler)
        assert await _client().put("k", "v", expiration_ttl=60) is True
        assert seen["params"]["expiration_ttl"] == 60
        assert seen["content"] == "v"

    @pytest.mark.asyncio
    async def test_put_no_ttl_params_empty(self, monkeypatch):
        seen = {}
        _patch_http(monkeypatch, lambda m, u, **kw: (seen.update(kw), _Resp(400))[1])
        assert await _client().put("k", "v") is False
        assert seen["params"] == {}

    @pytest.mark.asyncio
    async def test_put_json(self, monkeypatch):
        seen = {}
        _patch_http(monkeypatch, lambda m, u, **kw: (seen.update(kw), _Resp(200))[1])
        assert await _client().put_json("k", {"x": 2}) is True
        assert seen["content"] == '{"x": 2}'

    @pytest.mark.asyncio
    async def test_delete(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200))
        assert await _client().delete("k") is True
        assert await _client(enabled=False).delete("k") is False


class TestListKeys:
    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await _client(enabled=False).list_keys() == []

    @pytest.mark.asyncio
    async def test_pagination(self, monkeypatch):
        pages = [
            _Resp(200, data={"success": True, "result": [{"name": "a"}, {"name": "b"}], "result_info": {"cursor": "c2"}}),
            _Resp(200, data={"success": True, "result": [{"name": "c"}], "result_info": {"cursor": ""}}),
        ]
        calls = []

        def handler(m, u, **kw):
            calls.append(kw["params"].get("cursor"))
            return pages.pop(0)

        _patch_http(monkeypatch, handler)
        out = await _client().list_keys(prefix="p")
        assert out == ["a", "b", "c"]
        assert calls == [None, "c2"]

    @pytest.mark.asyncio
    async def test_non_200_breaks(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(500))
        assert await _client().list_keys() == []

    @pytest.mark.asyncio
    async def test_unsuccessful_breaks(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, data={"success": False}))
        assert await _client().list_keys() == []

    @pytest.mark.asyncio
    async def test_limit_cap(self, monkeypatch):
        _patch_http(
            monkeypatch,
            lambda m, u, **kw: _Resp(200, data={"success": True, "result": [{"name": f"k{i}"} for i in range(5)], "result_info": {"cursor": "more"}}),
        )
        out = await _client().list_keys(limit=3)
        assert out == ["k0", "k1", "k2"]


class TestHealth:
    @pytest.mark.asyncio
    async def test_disabled(self):
        assert await _client(enabled=False).health() is False

    @pytest.mark.asyncio
    async def test_ok(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, "ok" if m == "GET" else ""))
        assert await _client().health() is True

    @pytest.mark.asyncio
    async def test_mismatch_false(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, **kw: _Resp(200, "stale" if m == "GET" else ""))
        assert await _client().health() is False

    @pytest.mark.asyncio
    async def test_exception_false(self, monkeypatch):
        c = _client()
        c.put = AsyncMock(side_effect=RuntimeError("net"))
        assert await c.health() is False
