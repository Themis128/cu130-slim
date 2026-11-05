"""Coverage for kakkava — extraction, chunking, corpus iter, chroma plumbing."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.kakkava as K

pytestmark = pytest.mark.asyncio


def test_extract_bad_suffix_and_exception(tmp_path):
    assert K.extract_text(tmp_path / "f.bin") == ""
    p = tmp_path / "f.md"
    p.write_text("hello", encoding="utf-8")
    assert K.extract_text(p) == "hello"
    import unittest.mock as um
    with um.patch.object(Path, "read_text", side_effect=OSError("denied")):
        assert K.extract_text(p) == ""


def test_chunk_text_boundaries():
    assert K.chunk_text("") == []
    long_text = ("para one.\n\n" + "word " * 400 + "\n\npara tail.")
    chunks = K.chunk_text(long_text, size=200, overlap=20)
    assert len(chunks) > 1 and all(c.strip() for c in chunks)


def test_iter_corpus_non_dir(tmp_path):
    assert K.iter_corpus_files(tmp_path / "nope") == []
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.exe").write_text("x")
    assert [p.name for p in K.iter_corpus_files(tmp_path)] == ["a.md"]


async def test_get_existing_non_200(monkeypatch):
    client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=500)))
    assert await K._get_existing(client, "col1") == {}
    resp = SimpleNamespace(status_code=200, json=lambda: {"ids": ["i1"], "metadatas": [{"m": 1}]})
    client.post = AsyncMock(return_value=resp)
    out = await K._get_existing(client, "col1", where={"s": "x"})
    assert out == {"i1": {"m": 1}}


async def test_sync_no_collection(monkeypatch):
    import httpx

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: C())
    monkeypatch.setattr(K, "_get_collection_id", AsyncMock(return_value=None))
    monkeypatch.setattr(K, "iter_corpus_files", lambda root: [Path("a.md")])
    out = await K.sync_corpus("t1")
    assert out["errors"] == 1
