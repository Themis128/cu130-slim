"""Coverage: kakkava async Chroma helpers + recall."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _resp(payload=None, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = payload or {}
    r.raise_for_status = MagicMock()
    return r


@pytest.mark.asyncio
async def test_get_existing(monkeypatch):
    from app.services import kakkava as K

    monkeypatch.setattr(K, "_collection_base_url", lambda: "http://c/api/v1/collections")
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({"ids": ["a", "b"], "metadatas": [{"s": 1}, {"s": 2}]}))
    out = await K._get_existing(client, "col", {"x": 1})
    assert out == {"a": {"s": 1}, "b": {"s": 2}}


@pytest.mark.asyncio
async def test_upsert_chunks(monkeypatch):
    from app.services import kakkava as K

    monkeypatch.setattr(K, "_collection_base_url", lambda: "http://c")
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({}))
    n = await K._upsert_chunks(client, "col", ["i1", "i2"], [[0.1], [0.2]], ["d1", "d2"], [{}, {}])
    assert n == 2


@pytest.mark.asyncio
async def test_delete_ids(monkeypatch):
    from app.services import kakkava as K

    monkeypatch.setattr(K, "_collection_base_url", lambda: "http://c")
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({}))
    n = await K._delete_ids(client, "col", ["i1", "i2"])
    assert n == 2


@pytest.mark.asyncio
async def test_recall_doctrine(monkeypatch):
    from app.services import kakkava as K

    monkeypatch.setattr(K, "kakkava_enabled", lambda: True)
    monkeypatch.setattr(K, "_get_embedding", AsyncMock(return_value=[0.1, 0.2]))
    monkeypatch.setattr(K, "_doctrine_collection", lambda t: "col")
    monkeypatch.setattr(K, "_collection_base_url", lambda: "http://c")
    monkeypatch.setattr(K, "_get_collection_id", AsyncMock(return_value="cid"))
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp({"documents": [["doc1"]], "metadatas": [[{"source": "paper.pdf"}]], "distances": [[0.3]]}))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(K.httpx, "AsyncClient", lambda *a, **k: client)
    out = await K.recall_doctrine("t", "q")
    assert out and out[0]["source"] == "paper.pdf"


@pytest.mark.asyncio
async def test_doctrine_context(monkeypatch):
    from app.services import kakkava as K

    monkeypatch.setattr(
        K,
        "recall_doctrine",
        AsyncMock(return_value=[{"text": "doctrine a", "source": "p.pdf", "distance": 0.1}, {"text": "doctrine b", "source": "q.pdf", "distance": 0.2}]),
    )
    out = await K.doctrine_context("t", "q")
    assert "doctrine" in out
