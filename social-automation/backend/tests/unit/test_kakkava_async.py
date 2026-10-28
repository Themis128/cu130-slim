"""Coverage: kakkava async Chroma helpers + sync_corpus + recall."""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _resp(payload=None, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = payload or {}
    r.raise_for_status = MagicMock()
    return r


@pytest.mark.asyncio
async def test_get_existing_paginates(monkeypatch):
    from app.services import kakkava as K
    client = MagicMock()
    client.post = AsyncMock(side_effect=[
        _resp({"ids": ["a"], "documents": ["d"], "metadatas": [{}]}),
        _resp({"ids": [], "documents": [], "metadatas": []}),
    ])
    out = await K._get_existing(client, "col", {"x": 1})
    assert out["ids"] == ["a"]


@pytest.mark.asyncio
async def test_upsert_chunks(monkeypatch):
    from app.services import kakkava as K
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({}))
    n = await K._upsert_chunks(client, "col", ["i1"], [[0.1]], ["doc"], [{}])
    assert n == 1


@pytest.mark.asyncio
async def test_delete_ids(monkeypatch):
    from app.services import kakkava as K
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({}))
    n = await K._delete_ids(client, "col", ["i1", "i2"])
    assert n == 2


@pytest.mark.asyncio
async def test_sync_corpus_empty_root(monkeypatch, tmp_path):
    from app.services import kakkava as K
    monkeypatch.setattr(K, "kakkava_enabled", lambda: True)
    monkeypatch.setattr(K, "settings", MagicMock(CHROMA_URL="http://c"))
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp([]))
    client.post = AsyncMock(return_value=_resp({"id": "cid"}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: client)
    out = await K.sync_corpus("t", tmp_path)
    assert isinstance(out, dict)


@pytest.mark.asyncio
async def test_recall_doctrine(monkeypatch):
    from app.services import kakkava as K
    monkeypatch.setattr(K, "kakkava_enabled", lambda: True)
    monkeypatch.setattr(K, "settings", MagicMock(CHROMA_URL="http://c"))
    monkeypatch.setattr("app.services.chroma_client._get_embedding",
                        AsyncMock(return_value=[0.1, 0.2]))
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp([{"id": "cid"}]))
    client.post = AsyncMock(return_value=_resp(
        {"ids": [["i1"]], "documents": [["d"]], "metadatas": [[{"f": "x"}]]}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: client)
    out = await K.recall_doctrine("t", "q")
    assert isinstance(out, list)


@pytest.mark.asyncio
async def test_doctrine_context(monkeypatch):
    from app.services import kakkava as K
    monkeypatch.setattr(K, "recall_doctrine", AsyncMock(return_value=[
        {"text": "doctrine a"}, {"text": "doctrine b"}]))
    out = await K.doctrine_context("t", "q")
    assert "doctrine" in out
