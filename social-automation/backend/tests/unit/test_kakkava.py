"""Unit tests for app.services.kakkava — corpus walking, text extraction,
chunking, doc-id stability, and recall/context formatting.

No network, no database: Chroma HTTP calls and the embedding chain are
monkeypatched at the module boundary; PDF extraction exercises a real
PyMuPDF-written document.
"""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import kakkava

# ── chunking ────────────────────────────────────────────────────────────────


def test_chunk_text_empty():
    assert kakkava.chunk_text("") == []
    assert kakkava.chunk_text("   \n\n  ") == []


def test_chunk_text_short_single():
    assert kakkava.chunk_text("hello world") == ["hello world"]


def test_chunk_text_prefers_paragraph_breaks():
    para = "a" * 800
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = kakkava.chunk_text(text, size=1000, overlap=100)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 1000
    # every source char is covered by some chunk
    joined = "".join(chunks)
    assert "a" * 100 in joined


def test_chunk_text_overlap_progresses():
    text = "x" * 5000
    chunks = kakkava.chunk_text(text, size=1000, overlap=200)
    assert len(chunks) > 4
    assert all(len(c) <= 1000 for c in chunks)


# ── extraction ──────────────────────────────────────────────────────────────


def test_extract_text_md(tmp_path: Path):
    p = tmp_path / "note.md"
    p.write_text("# Title\n\nBody text.", encoding="utf-8")
    assert "Body text." in kakkava.extract_text(p)


def test_extract_text_html_strips_scripts(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(
        "<html><head><script>var x=1;</script></head><body><p>Hello doctrine</p></body></html>",
        encoding="utf-8",
    )
    out = kakkava.extract_text(p)
    assert "Hello doctrine" in out
    assert "var x" not in out


def test_extract_text_pdf(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    p = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Visibility Era teachings")
    doc.save(p)
    doc.close()
    assert "Visibility Era teachings" in kakkava.extract_text(p)


def test_extract_text_unknown_suffix(tmp_path: Path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01")
    assert kakkava.extract_text(p) == ""


def test_iter_corpus_files_filters(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.md").write_text("m", encoding="utf-8")
    (tmp_path / "c.zip").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.txt").write_text("t", encoding="utf-8")
    names = sorted(p.name for p in kakkava.iter_corpus_files(tmp_path))
    assert names == ["a.pdf", "b.md", "d.txt"]


# ── doc ids + collections ───────────────────────────────────────────────────


def test_doc_id_stable():
    a = kakkava._doc_id("DAY 1/x.pdf", 0)
    b = kakkava._doc_id("DAY 1/x.pdf", 0)
    c = kakkava._doc_id("DAY 1/x.pdf", 1)
    assert a == b != c


def test_doctrine_collection_name():
    col = kakkava._doctrine_collection("10024d25-2ea4-40ea-86c6-ba89f515a2eb")
    assert col == "team_10024d25_2ea4_40ea_86c6_ba89f515a2eb_doctrine"


def test_kakkava_enabled_off_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(kakkava.settings, "CHROMA_URL", "http://chroma:8000")
    monkeypatch.setattr(kakkava.settings, "KAKKAVA_DIR", str(tmp_path / "nope"))
    assert kakkava.kakkava_enabled() is False


# ── recall ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_doctrine_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(kakkava, "kakkava_enabled", lambda: False)
    assert await kakkava.recall_doctrine("t", "q") == []


@pytest.mark.asyncio
async def test_recall_doctrine_no_embedding_returns_empty(monkeypatch):
    monkeypatch.setattr(kakkava, "kakkava_enabled", lambda: True)
    monkeypatch.setattr(kakkava, "_get_embedding", AsyncMock(return_value=[]))
    assert await kakkava.recall_doctrine("t", "q") == []


@pytest.mark.asyncio
async def test_doctrine_context_formats_sources(monkeypatch):
    hits = [
        {"text": "Clarity attracts.", "source": "DAY 3/x.pdf", "distance": 0.1},
        {"text": "Vagueness gets ignored.", "source": "DAY 3/x.pdf", "distance": 0.2},
    ]
    monkeypatch.setattr(kakkava, "recall_doctrine", AsyncMock(return_value=hits))
    out = await kakkava.doctrine_context("t", "niche positioning")
    assert "SOURCE DOCTRINE" in out
    assert "Clarity attracts." in out
    assert "DAY 3/x.pdf" in out


@pytest.mark.asyncio
async def test_doctrine_context_empty_when_no_hits(monkeypatch):
    monkeypatch.setattr(kakkava, "recall_doctrine", AsyncMock(return_value=[]))
    assert await kakkava.doctrine_context("t", "q") == ""


@pytest.mark.asyncio
async def test_doctrine_context_respects_max_chars(monkeypatch):
    hits = [{"text": "x" * 5000, "source": "a.pdf", "distance": 0.1}]
    monkeypatch.setattr(kakkava, "recall_doctrine", AsyncMock(return_value=hits))
    out = await kakkava.doctrine_context("t", "q", max_chars=100)
    assert out == ""


# ── sync (boundary-faked Chroma) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_corpus_skips_unchanged_files(monkeypatch, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello corpus", encoding="utf-8")
    file_sha = hashlib.sha256(b"hello corpus").hexdigest()

    monkeypatch.setattr(kakkava, "_get_embedding", AsyncMock(return_value=[0.1] * 8))
    monkeypatch.setattr(kakkava, "_get_collection_id", AsyncMock(return_value="col-1"))
    get_existing = AsyncMock(return_value={"kk:x:0": {"file_sha": file_sha, "source": "a.md", "chunk": 0}})
    monkeypatch.setattr(kakkava, "_get_existing", get_existing)
    upsert = AsyncMock(return_value=0)
    monkeypatch.setattr(kakkava, "_upsert_chunks", upsert)
    monkeypatch.setattr(kakkava, "_delete_ids", AsyncMock(return_value=0))

    stats = await kakkava.sync_corpus("team-1", root=tmp_path)
    assert stats["unchanged"] == 1
    assert stats["chunks_added"] == 0
    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_sync_corpus_embeds_new_files(monkeypatch, tmp_path):
    (tmp_path / "a.md").write_text("fresh doctrine text", encoding="utf-8")

    monkeypatch.setattr(kakkava, "_get_embedding", AsyncMock(return_value=[0.1] * 8))
    monkeypatch.setattr(kakkava, "_get_collection_id", AsyncMock(return_value="col-1"))
    monkeypatch.setattr(kakkava, "_get_existing", AsyncMock(return_value={}))
    upsert = AsyncMock(return_value=1)
    monkeypatch.setattr(kakkava, "_upsert_chunks", upsert)
    monkeypatch.setattr(kakkava, "_delete_ids", AsyncMock(return_value=0))

    stats = await kakkava.sync_corpus("team-1", root=tmp_path)
    assert stats["chunks_added"] == 1
    assert upsert.await_count == 1
    # doc id derives from relpath
    _, _, ids, embeds, docs, metas = upsert.await_args.args
    assert ids[0] == kakkava._doc_id("a.md", 0)
    assert metas[0]["source"] == "a.md"


@pytest.mark.asyncio
async def test_sync_corpus_empty_dir(tmp_path):
    stats = await kakkava.sync_corpus("team-1", root=tmp_path)
    assert stats["files"] == 0


def test_worker_task_registered():
    from app.worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert "app.worker.tasks.kakkava_sync.sync_kakkava_corpus" in celery_app.tasks
