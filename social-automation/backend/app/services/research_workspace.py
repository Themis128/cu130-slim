"""Research workspace — Papers-AI-style full-context research surface.

A project holds research material (papers, notes, code, media docs).
Sources are pulled from a Nextcloud/WebDAV folder, chunked, embedded
into the team's `research` Chroma collection, and queryable with full
context — the AI sees every ingested doc when answering.
"""
import logging
import re
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.session import async_session_maker
from app.services.chroma_client import _get_embedding
from app.services.nextcloud_sync import _base_url, _client

logger = logging.getLogger(__name__)

_COLL = "research"
_CHUNK = 1200
_OVERLAP = 150
_EXTS = {".pdf", ".md", ".txt", ".py", ".ipynb", ".docx", ".html"}


def _chunks(text: str) -> list[str]:
    """Sentence-aware chunking with overlap."""
    parts, buf = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(buf) + len(sent) > _CHUNK and buf:
            parts.append(buf.strip())
            buf = buf[-_OVERLAP:]
        buf += " " + sent
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if len(p) > 40]


def _extract(name: str, raw: bytes) -> str:
    """PDF via pymupdf; everything else decodes as text."""
    if name.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(stream=raw, filetype="pdf")
        return "\n".join(p.get_text() for p in doc)
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _dav_client() -> httpx.AsyncClient:
    return _client()


async def _collection_id(team_id: str) -> str:
    """research_<team> collection, created if missing."""
    import httpx as _h

    from app.services.chroma_client import _collection_base_url, _collection_item_url, _validate_collection_name
    col = _validate_collection_name(f"{_COLL}_{team_id}")
    async with _h.AsyncClient(timeout=30) as c:
        r = await c.get(_collection_item_url(col))
        if r.status_code == 200:
            return r.json()["id"]
        r = await c.post(_collection_base_url(), json={"name": col})
        r.raise_for_status()
        return r.json()["id"]


async def _upsert(col_id: str, ids: list[str], embs: list, docs: list, metas: list):
    import httpx as _h

    from app.services.chroma_client import _collection_base_url
    base = _collection_base_url().rsplit("/collections", 1)[0]
    async with _h.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base}/collections/{col_id}/upsert",
                         json={"ids": ids, "embeddings": embs,
                               "documents": docs, "metadatas": metas})
        r.raise_for_status()


async def ingest_bytes(team_id: str, project: str, name: str, raw: bytes) -> dict:
    """Chunk + embed one file into the workspace."""
    text = _extract(name, raw)
    if not text.strip():
        return {"ingested": 0, "file": name}
    chunks = _chunks(text)[:200]
    embs = [await _get_embedding(c) for c in chunks]
    col_id = await _collection_id(team_id)
    ids = [f"{project}:{name}:{i}" for i in range(len(chunks))]
    metas = [{"project": project, "source": name, "chunk": i}
             for i in range(len(chunks))]
    await _upsert(col_id, ids, embs, chunks, metas)
    return {"ingested": len(chunks), "file": name}


async def ingest_nextcloud_folder(team_id: str, project: str, folder: str) -> dict:
    """Walk a Nextcloud folder via WebDAV and ingest every readable file."""
    total = 0
    files = []
    async with await _dav_client() as c:
        r = await c.request("PROPFIND", f"{_base_url()}/remote.php/dav/files/{settings.NEXTCLOUD_USERNAME}/{folder}",
                            headers={"Depth": "1"})
        r.raise_for_status()
        for m in re.findall(r"<d:href>([^<]+)</d:href>", r.text):
            p = Path(m).name
            if Path(p).suffix.lower() in _EXTS:
                files.append(m)
        for href in files:
            name = Path(href).name
            fr = await c.get(f"https://cloud.cloudless.gr{href}")
            if fr.status_code == 200:
                res = await ingest_bytes(team_id, project, name, fr.content)
                total += res["ingested"]
    return {"project": project, "files": len(files), "chunks": total}


async def _query(team_id: str, text: str, n: int) -> list[str]:
    """Similarity search the research_<team> collection only."""
    emb = await _get_embedding(text)
    if not emb:
        return []
    from app.services.chroma_client import _collection_base_url
    col_id = await _collection_id(team_id)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_collection_base_url()}/{col_id}/query",
                         json={"query_embeddings": [emb], "n_results": n})
        if r.status_code == 200:
            return r.json().get("documents", [[]])[0]
    return []


async def ask(team_id: str, question: str, n: int = 6) -> dict:
    """Full-context research Q&A — every ingested doc is in scope."""
    hits = await _query(team_id, question, n)
    ctx = "\n\n---\n\n".join(hits)
    import os
    dmr = os.environ.get("DMR_BASE_URL", "http://dmr:12434")
    r = await httpx.AsyncClient(timeout=90).post(
        f"{dmr}/engines/llamacpp/v1/chat/completions",
        json={"model": "ai/llama3.2", "messages": [
            {"role": "system", "content":
             "You are the research assistant for this workspace. Answer ONLY "
             "from the provided context; cite which source informed each "
             "claim. If the context lacks the answer, say so."},
            {"role": "user", "content":
             f"Context:\n{ctx}\n\nQuestion: {question}"}]})
    return {"answer": r.json()["choices"][0]["message"]["content"],
            "sources": len(hits)}


async def sync_all():
    """Hourly-ish sync — re-ingest each team's configured research folder."""
    async with async_session_maker() as db:
        from app.models.settings import Setting
        rows = (await db.execute(
            select(Setting).where(Setting.key == "research_folder"))).scalars().all()
    out = []
    for s in rows:
        try:
            out.append(await ingest_nextcloud_folder(
                s.team_id, s.data.get("project", "default"), s.data.get("folder", "Research")))
        except Exception as e:
            logger.warning("[research] sync failed for %s: %s", s.team_id, e)
    return out
