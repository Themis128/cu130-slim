"""Ingest the OneDrive 'Kakkava Sofia' folder into the team company brain.

Runs INSIDE social-api (needs app.services.company_brain + fitz/PyMuPDF).
Expects KAKKAVA_ROOT (default /tmp/kakkava-sofia) already extracted.
For every text-bearing file (pdf/md/html/txt) it extracts text, chunks it,
and upserts each chunk into Chroma via company_brain.remember with doc_id
kakkava-sofia-<slug>-<hash>-<i> (idempotent across re-runs). Finally writes
a Nextcloud index note under Company Brain.

Env: SOCIALAUTO_TEAM_ID (required), KAKKAVA_ROOT.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pymupdf  # PyMuPDF (fitz is deprecated)

sys.path.insert(0, "/app")
from app.services.company_brain import remember, _write_nextcloud_note  # noqa: E402

ROOT = Path(os.environ.get("KAKKAVA_ROOT", "/tmp/kakkava-sofia"))
TEAM_ID = os.environ["SOCIALAUTO_TEAM_ID"]
CHUNK = 2800
MAX_FILE_CHARS = 30000
TEXT_EXTS = {".pdf", ".md", ".markdown", ".html", ".htm", ".txt"}
SKIP_DIRS = {".git", "node_modules"}


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "doc"


def extract_pdf(path: Path) -> str:
    out = []
    try:
        with pymupdf.open(path) as doc:
            for page in doc:
                out.append(page.get_text())
    except Exception as exc:  # noqa: BLE001
        print(f"  ! pdf fail {path.name}: {exc}", file=sys.stderr)
    return "\n".join(out)


def extract_html(path: Path) -> str:
    raw = path.read_text(errors="replace")
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw)


def extract_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext in {".html", ".htm"}:
        return extract_html(path)
    return path.read_text(errors="replace")


def chunks(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= CHUNK:
        return [text] if text else []
    parts, start = [], 0
    while start < len(text):
        end = min(start + CHUNK, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + CHUNK // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + CHUNK // 2, end)
            if cut != -1:
                end = cut
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        start = end
    return parts


async def _existing_ids() -> set:
    """Doc ids already in the team brain collection — resume support.

    Upserts are idempotent but re-embedding every chunk costs minutes per
    file; skipping ids that already exist makes a killed run cheap to
    re-run (only the unfinished tail is embedded).
    """
    import httpx

    from app.services.chroma_client import (
        _CHROMA_TIMEOUT,
        _collection_base_url,
        _get_collection_id,
    )

    col = f"team_{TEAM_ID.replace('-', '_')}_brain"
    try:
        async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return set()
            r = await client.post(
                f"{_collection_base_url()}/{col_id}/get",
                json={"include": []},
            )
            if r.status_code != 200:
                return set()
            return set(r.json().get("ids") or [])
    except Exception:  # noqa: BLE001 — worst case is a full re-ingest
        return set()


async def main() -> None:
    files = sorted(
        p for p in ROOT.rglob("*")
        if p.is_file()
        and p.suffix.lower() in TEXT_EXTS
        and not any(part in SKIP_DIRS for part in p.parts)
    )
    existing = await _existing_ids()
    index_rows = []
    total_mem = 0
    skipped = 0
    for i, path in enumerate(files, 1):
        rel = str(path.relative_to(ROOT))
        text = extract_file(path)[:MAX_FILE_CHARS]
        parts = chunks(text)
        if not parts:
            index_rows.append((rel, 0, "no text extracted"))
            continue
        base = (
            f"kakkava-sofia-{slugify(rel)}-"
            f"{hashlib.md5(rel.encode()).hexdigest()[:6]}"
        )
        for j, piece in enumerate(parts):
            doc_id = f"{base}-{j}"
            if doc_id in existing:
                skipped += 1
                continue
            ok = await remember(
                TEAM_ID,
                doc_id,
                f"[Kakkava Sofia source: {rel}]\n\n{piece}",
                metadata={"kind": "kakkava-sofia", "source": rel},
            )
            if ok:
                existing.add(doc_id)
            total_mem += int(ok)
        index_rows.append((rel, len(parts), f"{len(text)} chars"))
        print(f"[{i}/{len(files)}] {rel} -> {len(parts)} chunks", flush=True)

    lines = [
        "# Kakkava Sofia — Source Library (Company Brain ingest)",
        "",
        f"Ingested {len(files)} documents from OneDrive/Kakkava Sofia into",
        "the team brain collection as `kakkava-sofia-*` memories.",
        "",
        "| File | Chunks | Size |",
        "|---|---|---|",
    ]
    lines += [f"| {r} | {c} | {s} |" for r, c, s in index_rows]
    note = "\n".join(lines)
    ok_idx = await remember(
        TEAM_ID, "kakkava-sofia-index", note,
        metadata={"kind": "kakkava-sofia", "source": "_index"},
    )
    note_ok = await _write_nextcloud_note("Kakkava Sofia — Source Library", note)
    print(json.dumps({
        "files": len(files), "memories": total_mem, "skipped_existing": skipped,
        "index_memory": ok_idx, "index_note": note_ok,
    }))


asyncio.run(main())
