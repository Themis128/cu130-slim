"""Kakkava doctrine corpus — the Visibility Era source material as retrievable context.

The coach material lives in a host folder (OneDrive "Kakkava Sofia") that is
bind-mounted read-only at ``settings.KAKKAVA_DIR``. This service:

* **Ingest** — walks the folder (pdf/md/txt/html), extracts text, chunks it,
  embeds each chunk through the existing DMR → Cloudflare BGE-M3 chain, and
  upserts into the per-team Chroma collection ``team_<id>_doctrine``.
  Idempotent: file content hashes are stored in chunk metadata, unchanged
  files are skipped, deleted files/pruned chunks are removed.
* **Read** — ``recall_doctrine()`` returns the most relevant source chunks
  for a generation brief so prompts cite the real doctrine instead of relying
  on baked-in summaries.

Every path is fail-silent: doctrine is advisory context, never on the publish
path. A missing folder or unreachable Chroma yields empty recall.
"""

from __future__ import annotations

import hashlib
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.services.chroma_client import (
    _CHROMA_TIMEOUT,
    _collection_base_url,
    _get_collection_id,
    _get_embedding,
)

logger = logging.getLogger(__name__)

settings = get_settings()

DOCTRINE_COLLECTION_SUFFIX = "doctrine"
_CHUNK_SIZE = 1400
_CHUNK_OVERLAP = 200
_UPSERT_BATCH = 25
_SUPPORTED = {".pdf", ".md", ".txt", ".html", ".htm"}


def kakkava_enabled() -> bool:
    return bool(settings.KAKKAVA_SYNC_ENABLED and (settings.CHROMA_URL or "").strip() and Path(settings.KAKKAVA_DIR).is_dir())


def _doctrine_collection(team_id) -> str:
    return f"team_{str(team_id).replace('-', '_')}_{DOCTRINE_COLLECTION_SUFFIX}"


def _doc_id(relpath: str, chunk_idx: int) -> str:
    return f"kk:{hashlib.sha1(relpath.encode()).hexdigest()[:16]}:{chunk_idx}"


# ── extraction ───────────────────────────────────────────────────────────────


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _extract_pdf(path: Path) -> str:
    import pymupdf

    out: list[str] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            out.append(page.get_text())
    return "\n".join(out)


def _extract_html(path: Path) -> str:
    stripper = _HTMLStripper()
    stripper.feed(path.read_text(encoding="utf-8", errors="replace"))
    return " ".join(stripper.parts)


def extract_text(path: Path) -> str:
    """Best-effort text extraction. Returns "" for unreadable/binary files."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix in (".html", ".htm"):
            return _extract_html(path)
        if suffix in (".md", ".txt"):
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("kakkava extract failed %s: %s", path, exc)
    return ""


def chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split cleaned text into overlapping windows that prefer paragraph breaks."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer to break on a paragraph or sentence boundary inside the
            # last third of the window rather than mid-sentence.
            window = text[start:end]
            for sep in ("\n\n", "\n", ". "):
                cut = window.rfind(sep, size // 2)
                if cut > 0:
                    end = start + cut + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def iter_corpus_files(root: Path) -> list[Path]:
    """All supported files under root, sorted for deterministic ordering."""
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _SUPPORTED),
        key=lambda p: str(p).lower(),
    )


# ── Chroma plumbing ──────────────────────────────────────────────────────────


async def _get_existing(client: httpx.AsyncClient, col_id: str, where: dict | None = None) -> dict:
    """Return {id: metadata} for docs matching a where filter (None = all)."""
    body: dict = {"include": ["metadatas"], "limit": 10000}
    if where:
        body["where"] = where
    resp = await client.post(f"{_collection_base_url()}/{col_id}/get", json=body)
    if resp.status_code != 200:
        return {}
    data = resp.json()
    ids = data.get("ids") or []
    metas = data.get("metadatas") or []
    return dict(zip(ids, metas, strict=False))


async def _upsert_chunks(client: httpx.AsyncClient, col_id: str, ids, embeds, docs, metas) -> int:
    sent = 0
    for i in range(0, len(ids), _UPSERT_BATCH):
        resp = await client.post(
            f"{_collection_base_url()}/{col_id}/upsert",
            json={
                "ids": ids[i : i + _UPSERT_BATCH],
                "embeddings": embeds[i : i + _UPSERT_BATCH],
                "documents": docs[i : i + _UPSERT_BATCH],
                "metadatas": metas[i : i + _UPSERT_BATCH],
            },
        )
        if resp.status_code == 200:
            sent += len(ids[i : i + _UPSERT_BATCH])
        else:
            logger.warning("kakkava upsert batch failed: %s", resp.status_code)
    return sent


async def _delete_ids(client: httpx.AsyncClient, col_id: str, ids: list[str]) -> int:
    if not ids:
        return 0
    resp = await client.post(f"{_collection_base_url()}/{col_id}/delete", json={"ids": ids})
    return len(ids) if resp.status_code == 200 else 0


# ── sync ─────────────────────────────────────────────────────────────────────


async def sync_corpus(team_id, root: Path | None = None) -> dict:
    """Ingest the Kakkava folder into the team's doctrine collection.

    Returns a stats dict; every failure mode degrades to logged warnings so a
    bad file or missing folder never breaks the sweep.
    """
    stats = {"files": 0, "unchanged": 0, "chunks_added": 0, "chunks_removed": 0, "errors": 0}
    root = root or Path(settings.KAKKAVA_DIR)
    files = iter_corpus_files(root)
    if not files:
        return stats

    col = _doctrine_collection(team_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(_CHROMA_TIMEOUT, read=60.0)) as client:
        col_id = await _get_collection_id(client, col)
        if not col_id:
            logger.warning("kakkava sync: no doctrine collection for team %s", team_id)
            stats["errors"] += 1
            return stats

        live_sources: set[str] = set()
        for path in files:
            stats["files"] += 1
            relpath = str(path.relative_to(root))
            live_sources.add(relpath)
            try:
                raw = path.read_bytes()
            except Exception as exc:
                logger.warning("kakkava read failed %s: %s", path, exc)
                stats["errors"] += 1
                continue
            file_sha = hashlib.sha256(raw).hexdigest()

            try:
                existing = await _get_existing(client, col_id, {"source": relpath})
            except Exception:
                existing = {}
            if existing and all(m.get("file_sha") == file_sha for m in existing.values()):
                stats["unchanged"] += 1
                continue

            text = extract_text(path)
            chunks = chunk_text(text)
            if not chunks:
                stats["errors"] += 1
                continue

            ids, embeds, docs, metas = [], [], [], []
            for idx, chunk in enumerate(chunks):
                emb = await _get_embedding(chunk)
                if not emb:
                    continue
                ids.append(_doc_id(relpath, idx))
                embeds.append(emb)
                docs.append(chunk)
                metas.append(
                    {
                        "source": relpath,
                        "folder": str(Path(relpath).parent),
                        "chunk": idx,
                        "file_sha": file_sha,
                    }
                )
            if embeds:
                stats["chunks_added"] += await _upsert_chunks(client, col_id, ids, embeds, docs, metas)
            # Drop stale chunks beyond the current chunk count (file shrank).
            stale = [doc_id for doc_id, m in existing.items() if (m.get("chunk") or 0) >= len(chunks)]
            stats["chunks_removed"] += await _delete_ids(client, col_id, stale)

        # Drop docs whose source file was deleted from the folder.
        try:
            all_docs = await _get_existing(client, col_id)
            orphaned = [doc_id for doc_id, m in all_docs.items() if m.get("source") not in live_sources]
            stats["chunks_removed"] += await _delete_ids(client, col_id, orphaned)
        except Exception as exc:
            logger.warning("kakkava orphan sweep failed: %s", exc)

    return stats


# ── recall ───────────────────────────────────────────────────────────────────


async def recall_doctrine(team_id, query: str, n_results: int = 4) -> list[dict]:
    """Return the most relevant doctrine chunks for a brief.

    Each hit is {text, source, distance}. Empty on any failure — generation
    must never block on doctrine availability.
    """
    if not kakkava_enabled():
        return []
    embedding = await _get_embedding(query)
    if not embedding:
        return []

    col = _doctrine_collection(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return []
            resp = await client.post(
                f"{_collection_base_url()}/{col_id}/query",
                json={
                    "query_embeddings": [embedding],
                    "n_results": n_results,
                    "include": ["documents", "metadatas", "distances"],
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            docs = (data.get("documents") or [[]])[0]
            metas = (data.get("metadatas") or [[]])[0]
            dists = (data.get("distances") or [[]])[0]
            return [{"text": d, "source": m.get("source", "?"), "distance": dist} for d, m, dist in zip(docs, metas, dists, strict=False) if d]
        except Exception as exc:
            logger.warning("kakkava recall failed: %s", exc)
            return []


async def doctrine_context(team_id, query: str, n_results: int = 4, max_chars: int = 1800) -> str:
    """Ready-to-inject prompt section quoting the most relevant source material."""
    hits = await recall_doctrine(team_id, query, n_results)
    if not hits:
        return ""
    lines: list[str] = []
    total = 0
    for h in hits:
        snippet = h["text"].strip()
        if total + len(snippet) > max_chars:
            break
        lines.append(f'— from "{h["source"]}":\n{snippet}')
        total += len(snippet)
    if not lines:
        return ""
    return (
        "\n\nSOURCE DOCTRINE (Sofia Kakkava Visibility Era material — ground the "
        "piece in these teachings, do not quote verbatim, do not name her in the copy):\n" + "\n\n".join(lines) + "\n"
    )
