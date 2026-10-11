"""Coverage for app/services/chroma_client.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chroma_client as C


def _settings(**kw):
    d = dict(CLOUDFLARE_AI_API_TOKEN="", CLOUDFLARE_API_TOKEN="", CLOUDFLARE_ACCOUNT_ID="", CHROMA_URL="http://chroma:8000")
    d.update(kw)
    return SimpleNamespace(**d)


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


class _HTTP:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self.handler("GET", url, kw)

    async def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.handler("POST", url, kw)


def _http(monkeypatch, handler):
    holder = {}

    def _factory(**kw):
        c = _HTTP(handler)
        holder["c"] = c
        return c

    monkeypatch.setattr(C.httpx, "AsyncClient", _factory)
    return holder


class TestValidate:
    def test_valid(self):
        assert C._validate_collection_name("team_abc_123_content") == "team_abc_123_content"

    def test_invalid(self):
        for bad in ["../etc", "team_x", "foo_content", "a" * 200, "team_../_content"]:
            with pytest.raises(ValueError):
                C._validate_collection_name(bad)


class TestCfAiToken:
    def test_prefers_ai(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_AI_API_TOKEN=" ai ", CLOUDFLARE_API_TOKEN="api"))
        assert C._cf_ai_token() == "ai"

    def test_fallback(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_API_TOKEN=" api "))
        assert C._cf_ai_token() == "api"

    def test_empty(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        assert C._cf_ai_token() == ""


class TestCfEmbedding:
    @pytest.mark.asyncio
    async def test_no_creds(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        assert await C._cf_embedding("t") == []

    @pytest.mark.asyncio
    async def test_result_data_nested(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_API_TOKEN="t"))
        _http(monkeypatch, lambda m, u, kw: _Resp(200, {"result": {"data": [[0.1, 0.2]], "shape": [1, 2]}}))
        assert await C._cf_embedding("t") == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_flat_data(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_ACCOUNT_ID="a", CLOUDFLARE_API_TOKEN="t"))
        _http(monkeypatch, lambda m, u, kw: _Resp(200, {"result": {"data": [0.5, 0.6]}}))
        assert await C._cf_embedding("t") == [0.5, 0.6]

    @pytest.mark.asyncio
    async def test_embeddings_key_and_top_level(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_ACCOUNT_ID="a", CLOUDFLARE_API_TOKEN="t"))
        _http(monkeypatch, lambda m, u, kw: _Resp(200, {"embeddings": [0.9]}))
        assert await C._cf_embedding("t") == [0.9]

    @pytest.mark.asyncio
    async def test_non_200_and_exception(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CLOUDFLARE_ACCOUNT_ID="a", CLOUDFLARE_API_TOKEN="t"))
        _http(monkeypatch, lambda m, u, kw: _Resp(500))
        assert await C._cf_embedding("t") == []

        def _boom(m, u, kw):
            raise RuntimeError("net")

        _http(monkeypatch, _boom)
        assert await C._cf_embedding("t") == []


class TestDmrAndGetEmbedding:
    @pytest.mark.asyncio
    async def test_dmr_ok(self, monkeypatch):
        import app.services.dmr as dmr

        monkeypatch.setattr(dmr, "call_dmr_embedding", AsyncMock(return_value=[1.0]))
        assert await C._dmr_embedding("t") == [1.0]
        assert await C._get_embedding("t") == [1.0]

    @pytest.mark.asyncio
    async def test_dmr_exception(self, monkeypatch):
        import app.services.dmr as dmr

        monkeypatch.setattr(dmr, "call_dmr_embedding", AsyncMock(side_effect=RuntimeError("x")))
        assert await C._dmr_embedding("t") == []

    @pytest.mark.asyncio
    async def test_get_falls_to_cf(self, monkeypatch):
        monkeypatch.setattr(C, "_dmr_embedding", AsyncMock(return_value=[]))
        monkeypatch.setattr(C, "_cf_embedding", AsyncMock(return_value=[2.0]))
        assert await C._get_embedding("t") == [2.0]


class TestUrls:
    def test_collection_name(self):
        tid = "11111111-2222-3333-4444-555555555555"
        assert C._collection_name(tid) == ("team_11111111_2222_3333_4444_555555555555_content")

    def test_base_url(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings(CHROMA_URL="http://chroma:8000/"))
        url = C._collection_base_url()
        assert url.endswith("/collections")
        assert url.startswith("http://chroma:8000")

    @pytest.mark.parametrize("bad", ["", "notaurl", "ftp://x", "http://"])
    def test_base_url_invalid(self, monkeypatch, bad):
        monkeypatch.setattr(C, "settings", _settings(CHROMA_URL=bad))
        with pytest.raises(ValueError):
            C._collection_base_url()

    def test_item_url(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        url = C._collection_item_url("team_a_b_content")
        assert url.endswith("/team_a_b_content")


class TestGetCollectionId:
    @pytest.mark.asyncio
    async def test_invalid_name(self):
        assert await C._get_collection_id(object(), "bad") is None

    @pytest.mark.asyncio
    async def test_existing(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        client = _HTTP(lambda m, u, kw: _Resp(200, {"id": "cid"}))
        assert await C._get_collection_id(client, "team_a_b_content") == "cid"

    @pytest.mark.asyncio
    async def test_created_after_get_fail(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(404), _Resp(201, {"id": "new"})])
        client = _HTTP(lambda m, u, kw: next(resps))
        assert await C._get_collection_id(client, "team_a_b_content") == "new"

    @pytest.mark.asyncio
    async def test_get_raises_then_create(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([RuntimeError("x"), _Resp(200, {"id": "c"})])

        def h(m, u, kw):
            r = next(resps)
            if isinstance(r, Exception):
                raise r
            return r

        assert await C._get_collection_id(_HTTP(h), "team_a_b_content") == "c"

    @pytest.mark.asyncio
    async def test_all_fail_none(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(404), _Resp(500)])
        client = _HTTP(lambda m, u, kw: next(resps))
        assert await C._get_collection_id(client, "team_a_b_content") is None

    @pytest.mark.asyncio
    async def test_create_raises_none(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(404), RuntimeError("x")])

        def h(m, u, kw):
            r = next(resps)
            if isinstance(r, Exception):
                raise r
            return r

        assert await C._get_collection_id(_HTTP(h), "team_a_b_content") is None


def _embed(monkeypatch, vec=None):
    monkeypatch.setattr(C, "_get_embedding", AsyncMock(return_value=vec if vec is not None else [0.1]))


class TestAddContent:
    @pytest.mark.asyncio
    async def test_no_embedding_returns(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch, vec=[])
        await C.add_content("t-x_content", "p", "text")  # no raise

    @pytest.mark.asyncio
    async def test_adds(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch)
        resps = iter([_Resp(200, {"id": "cid"}), _Resp(200)])
        holder = _http(monkeypatch, lambda m, u, kw: next(resps))
        await C.add_content("a-b", "p1", "text")
        assert holder["c"].calls[-1][1].endswith("/cid/add")

    @pytest.mark.asyncio
    async def test_no_collection(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch)
        resps = iter([_Resp(404), _Resp(500)])
        holder = _http(monkeypatch, lambda m, u, kw: next(resps))
        await C.add_content("a-b", "p1", "text")
        assert not any("/add" in u for _, u in holder["c"].calls)

    @pytest.mark.asyncio
    async def test_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch)

        # collection resolves, then the /add POST blows up → outer except
        resps = iter([_Resp(200, {"id": "cid"}), RuntimeError("x")])

        def h(m, u, kw):
            r = next(resps)
            if isinstance(r, Exception):
                raise r
            return r

        _http(monkeypatch, h)
        await C.add_content("a-b", "p1", "text")  # no raise


class TestGetContent:
    @pytest.mark.asyncio
    async def test_doc(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(200, {"id": "cid"}), _Resp(200, {"documents": ["hello"]})])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.get_content("a-b", "p1") == "hello"

    @pytest.mark.asyncio
    async def test_no_collection(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(404), _Resp(500)])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.get_content("a-b", "p") is None

    @pytest.mark.asyncio
    async def test_empty_docs(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(200, {"id": "c"}), _Resp(200, {"documents": []})])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.get_content("a-b", "p") is None

    @pytest.mark.asyncio
    async def test_non_200_and_exception(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        resps = iter([_Resp(200, {"id": "c"}), _Resp(500)])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.get_content("a-b", "p") is None

        # collection resolves, then the /get POST raises → outer except
        resps3 = iter([_Resp(200, {"id": "c"}), RuntimeError("x")])

        def h(m, u, kw):
            r = next(resps3)
            if isinstance(r, Exception):
                raise r
            return r

        _http(monkeypatch, h)
        assert await C.get_content("a-b", "p") is None


class TestQuerySimilar:
    @pytest.mark.asyncio
    async def test_no_embedding(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch, vec=[])
        assert await C.query_similar("a-b", "t") == []

    @pytest.mark.asyncio
    async def test_results(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch)
        resps = iter([_Resp(200, {"id": "c"}), _Resp(200, {"documents": [["d1", "d2"]]})])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.query_similar("a-b", "t", 3) == ["d1", "d2"]

    @pytest.mark.asyncio
    async def test_no_collection_and_errors(self, monkeypatch):
        monkeypatch.setattr(C, "settings", _settings())
        _embed(monkeypatch)
        resps = iter([_Resp(404), _Resp(500)])
        _http(monkeypatch, lambda m, u, kw: next(resps))
        assert await C.query_similar("a-b", "t") == []

        resps2 = iter([_Resp(200, {"id": "c"}), _Resp(500)])
        _http(monkeypatch, lambda m, u, kw: next(resps2))
        assert await C.query_similar("a-b", "t") == []

        # collection resolves, then the /query POST raises → outer except
        resps4 = iter([_Resp(200, {"id": "c"}), RuntimeError("x")])

        def h2(m, u, kw):
            r = next(resps4)
            if isinstance(r, Exception):
                raise r
            return r

        _http(monkeypatch, h2)
        assert await C.query_similar("a-b", "t") == []
