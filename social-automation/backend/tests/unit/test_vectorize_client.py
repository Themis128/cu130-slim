"""Coverage for app/services/vectorize_client.py."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.vectorize_client as VC


def _client(monkeypatch, **kw):
    s = SimpleNamespace(
        CLOUDFLARE_ACCOUNT_ID="acc",
        VECTORIZE_INDEX_NAME="idx",
        CLOUDFLARE_API_TOKEN="t1",
        CLOUDFLARE_AI_API_TOKEN="t2",
        CLOUDFLARE_EMAIL_API_TOKEN="t3")
    for k, v in kw.items():
        setattr(s, k, v)
    monkeypatch.setattr(VC, "settings", s)
    return VC.VectorizeClient()


def _resp(status, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text,
                           json=lambda: body or {})


class _HTTP:
    def __init__(self, resps):
        self._resps = list(resps)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resps.pop(0)

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resps.pop(0)


def _client_for(c):
    return lambda **kw: c


class TestInit:
    def test_token_chain(self, monkeypatch):
        c = _client(monkeypatch)
        assert c.api_token == "t1"
        assert c._tokens == ["t1", "t2", "t3"]

    def test_primary_missing_uses_ai(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_API_TOKEN="")
        assert c.api_token == "t2"

    def test_no_tokens_disabled(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_API_TOKEN="",
                    CLOUDFLARE_AI_API_TOKEN="",
                    CLOUDFLARE_EMAIL_API_TOKEN="")
        assert c.api_token == ""
        assert c.enabled is False

    def test_missing_account_disabled(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_ACCOUNT_ID="")
        assert c.enabled is False

    def test_base_url_and_headers(self, monkeypatch):
        c = _client(monkeypatch)
        assert "accounts/acc/vectorize/v2/indexes/idx" in c.base_url
        assert c._headers()["Authorization"] == "Bearer t1"
        c._active_token = "t9"
        assert c._headers()["Authorization"] == "Bearer t9"


class TestTryTokens:
    @pytest.mark.asyncio
    async def test_first_token_works(self, monkeypatch):
        c = _client(monkeypatch)
        method = AsyncMock(return_value=_resp(200))
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 200
        assert method.await_count == 1

    @pytest.mark.asyncio
    async def test_401_tries_next(self, monkeypatch):
        c = _client(monkeypatch)
        method = AsyncMock(side_effect=[_resp(401), _resp(200)])
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 200
        assert method.await_count == 2
        # second call used t2
        assert "t2" in method.await_args.kwargs["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_all_401_returns_last(self, monkeypatch):
        c = _client(monkeypatch)
        method = AsyncMock(return_value=_resp(401))
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 401
        assert c._active_token is None

    @pytest.mark.asyncio
    async def test_exceptions_continue_all_fail(self, monkeypatch):
        c = _client(monkeypatch)
        method = AsyncMock(side_effect=OSError("net"))
        with pytest.raises(RuntimeError, match="All Cloudflare tokens"):
            await c._try_tokens(method, "u")
        assert method.await_count == 3

    @pytest.mark.asyncio
    async def test_exception_then_401_returns_last_resp(self, monkeypatch):
        c = _client(monkeypatch)
        method = AsyncMock(side_effect=[OSError("x"), _resp(401),
                                        OSError("y")])
        resp = await c._try_tokens(method, "u")
        assert resp.status_code == 401


class TestOperations:
    @pytest.mark.asyncio
    async def test_upsert_disabled(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_API_TOKEN="",
                    CLOUDFLARE_AI_API_TOKEN="",
                    CLOUDFLARE_EMAIL_API_TOKEN="")
        assert await c.upsert("id", [0.1]) is False

    @pytest.mark.asyncio
    async def test_disabled_all_ops(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_API_TOKEN="",
                    CLOUDFLARE_AI_API_TOKEN="",
                    CLOUDFLARE_EMAIL_API_TOKEN="")
        assert await c.upsert_many([{"id": "a"}]) == 0
        assert await c.query([0.1]) == []
        assert await c.get_vectors(["a"]) == []
        assert await c.delete_vectors(["a"]) is False

    @pytest.mark.asyncio
    async def test_upsert_success_with_metadata(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.upsert("v1", [0.1], namespace="ns",
                              metadata={"k": "v"}) is True
        body = http.calls[0][2]["json"]
        assert body["vectors"][0]["id"] == "v1"
        assert body["vectors"][0]["metadata"] == {"k": "v"}
        assert body["vectors"][0]["namespace"] == "ns"
        assert "/upsert" in http.calls[0][1]

    @pytest.mark.asyncio
    async def test_upsert_failure(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(500)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.upsert("v1", [0.1]) is False

    @pytest.mark.asyncio
    async def test_upsert_many(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        vecs = [{"id": "a", "values": [1.0]},
                {"id": "b", "values": [2.0], "namespace": "custom"}]
        assert await c.upsert_many(vecs, namespace="ns") == 2
        body = http.calls[0][2]["json"]
        assert body["vectors"][0]["namespace"] == "ns"
        assert body["vectors"][1]["namespace"] == "custom"

    @pytest.mark.asyncio
    async def test_upsert_many_fail(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(500, text="bad")])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.upsert_many([{"id": "a", "values": []}]) == 0

    @pytest.mark.asyncio
    async def test_query_matches(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {
            "success": True,
            "result": {"matches": [{"id": "m1", "score": 0.9}]}})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        out = await c.query([0.1], top_k=3, return_metadata=False)
        assert out == [{"id": "m1", "score": 0.9}]
        body = http.calls[0][2]["json"]
        assert body["topK"] == 3 and body["return_metadata"] is False

    @pytest.mark.asyncio
    async def test_query_http_fail(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(500, text="e")])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.query([0.1]) == []

    @pytest.mark.asyncio
    async def test_query_unsuccessful_body(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {"success": False})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.query([0.1]) == []

    @pytest.mark.asyncio
    async def test_get_vectors(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {
            "result": {"vectors": [{"id": "a"}]}})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.get_vectors(["a"], namespace="ns") == [{"id": "a"}]
        assert http.calls[0][2]["json"] == {"ids": ["a"],
                                            "namespace": "ns"}

    @pytest.mark.asyncio
    async def test_get_vectors_fail(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(404)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.get_vectors(["a"]) == []

    @pytest.mark.asyncio
    async def test_delete_vectors(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.delete_vectors(["a"]) is True
        assert "delete_by_ids" in http.calls[0][1]

    @pytest.mark.asyncio
    async def test_delete_fail(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(500)])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.delete_vectors(["a"]) is False


class TestHealth:
    @pytest.mark.asyncio
    async def test_disabled(self, monkeypatch):
        c = _client(monkeypatch, CLOUDFLARE_ACCOUNT_ID="")
        assert await c.health() is False

    @pytest.mark.asyncio
    async def test_index_found(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {
            "success": True,
            "result": [{"name": "idx"}, {"name": "other"}]})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.health() is True

    @pytest.mark.asyncio
    async def test_index_not_found(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {
            "success": True, "result": [{"name": "other"}]})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.health() is False

    @pytest.mark.asyncio
    async def test_api_unsuccessful(self, monkeypatch):
        c = _client(monkeypatch)
        http = _HTTP([_resp(200, {"success": False})])
        monkeypatch.setattr(VC.httpx, "AsyncClient", _client_for(http))
        assert await c.health() is False

    @pytest.mark.asyncio
    async def test_exception(self, monkeypatch):
        c = _client(monkeypatch)
        c._tokens = []  # _try_tokens raises RuntimeError
        monkeypatch.setattr(VC.httpx, "AsyncClient",
                            lambda **kw: _HTTP([]))
        assert await c.health() is False
