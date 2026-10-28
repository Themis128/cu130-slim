"""Coverage: research_workspace async helpers (collection/upsert/query/ask/sync)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


class _R:
    def __init__(self, status=200, payload=None, text="", content=b""):
        self.status_code = status
        self._p = payload or {}
        self._text = text
        self.content = content

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("e", request=None, response=None)

    @property
    def text(self):
        return self._text


class _C:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self.responses.pop(0)

    async def post(self, *a, **k):
        return self.responses.pop(0)

    async def request(self, *a, **k):
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_collection_id_get_or_create(monkeypatch):
    from app.services import chroma_client as CC
    from app.services import research_workspace as RW
    monkeypatch.setattr(CC, "_collection_item_url", lambda n: f"http://c/{n}")
    monkeypatch.setattr(CC, "_collection_base_url", lambda: "http://c")
    monkeypatch.setattr(CC, "_validate_collection_name", lambda n: n)
    # found
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C([_R(200, {"id": "c1"})]))
    assert await RW._collection_id("t1") == "c1"
    # missing → create
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C([_R(404), _R(200, {"id": "c2"})]))
    assert await RW._collection_id("t1") == "c2"


@pytest.mark.asyncio
async def test_upsert(monkeypatch):
    from app.services import chroma_client as CC
    from app.services import research_workspace as RW
    monkeypatch.setattr(CC, "_collection_base_url", lambda: "http://c/collections")
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C([_R(200)]))
    await RW._upsert("col", ["i1"], [[0.1]], ["d"], [{}])  # no raise


@pytest.mark.asyncio
async def test_query_hits_and_misses(monkeypatch):
    from app.services import research_workspace as RW
    monkeypatch.setattr(RW, "_get_embedding", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(RW, "_collection_id", AsyncMock(return_value="col"))
    from app.services import chroma_client as CC
    monkeypatch.setattr(CC, "_collection_base_url", lambda: "http://c")
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C([_R(200, {"documents": [["doc1", "doc2"]]})]))
    assert await RW._query("t", "q", 2) == ["doc1", "doc2"]
    # no embedding → []
    monkeypatch.setattr(RW, "_get_embedding", AsyncMock(return_value=None))
    assert await RW._query("t", "q", 2) == []


@pytest.mark.asyncio
async def test_ask(monkeypatch):
    from app.services import research_workspace as RW
    monkeypatch.setattr(RW, "_query", AsyncMock(return_value=["ctx1", "ctx2"]))
    payload = {"choices": [{"message": {"content": "the answer"}}]}
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C([_R(200, payload)]))
    out = await RW.ask("t", "what?")
    assert out == {"answer": "the answer", "sources": 2}


@pytest.mark.asyncio
async def test_ingest_nextcloud_folder(monkeypatch):
    from app.services import research_workspace as RW
    propfind = _R(200, text="<d:href>/f/a.md</d:href>")
    fr = _R(200, content=b"file body")

    class _Dav:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            return propfind

        async def get(self, *a, **k):
            return fr

    monkeypatch.setattr(RW, "_dav_client", AsyncMock(return_value=_Dav()))
    monkeypatch.setattr(RW, "_base_url", lambda: "http://nc")
    monkeypatch.setattr(RW, "settings", MagicMock(NEXTCLOUD_USERNAME="u"))
    monkeypatch.setattr(RW, "ingest_bytes", AsyncMock(return_value={"ingested": 3}))
    out = await RW.ingest_nextcloud_folder("t", "p", "folder")
    assert out["files"] == 1 and out["chunks"] == 3


@pytest.mark.asyncio
async def test_sync_all_swallows_errors(monkeypatch):
    from app.services import research_workspace as RW
    res = MagicMock()
    row = MagicMock(value=json.dumps([
        {"team_id": "t1", "project": "p", "folder": "f"},
        {"team_id": "t2"},
    ]))
    res.scalars.return_value.first.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)

    class _S:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(RW, "async_session_maker", lambda: _S())
    calls = {"n": 0}

    async def _ing(t, p, f):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("down")
        return {"files": 1}
    monkeypatch.setattr(RW, "ingest_nextcloud_folder", _ing)
    out = await RW.sync_all()
    assert len(out) == 1  # second team's failure swallowed
