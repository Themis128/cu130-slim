"""Mirror repo documentation/architecture into Nextcloud Files.

The worker containers don't mount the repo checkout, so the doc manifest is
read from GitHub ``master`` — the merged source of truth — via the git trees
API. Changed files are uploaded to ``SocialAuto/docs/repo/<path>`` over WebDAV
and per-file blob SHAs are tracked in Redis, so repeat runs only push what
actually changed. A ``_manifest.json`` is written every run for provenance.

Runs on a 6h beat (``sync-nextcloud-docs``); safe to trigger manually after
merging doc/architecture changes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.services import nextcloud_export
from app.services.nextcloud_extras import _cursor, _set_cursor

logger = logging.getLogger(__name__)

REPO = "Themis128/cu130-slim"
REF = "master"
_REMOTE_PREFIX = "docs/repo"

# Doc surfaces mirrored into the workspace. Root-level *.md plus everything
# under these prefixes — skills' SKILL.md files count as operational docs.
# .cursor/ mirrors .devin/ skills; both are kept so the workspace shows the
# exact files each agent runtime reads.
_DOC_PREFIXES = ("docs/", "deploy/", ".devin/", ".cursor/", "social-automation/")

# Guardrails: skip blobs above this size and abort the manifest if the repo
# tree ever grows past this many candidate docs (fail loud, not partial-forever).
_MAX_FILE_BYTES = 512_000
_MAX_FILES = 500

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "socialauto-nextcloud-docs/1.0",
}


def nextcloud_docs_enabled() -> bool:
    return nextcloud_export.nextcloud_export_enabled()


def _is_doc_path(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    if "/" not in path:
        return True  # root-level markdown (AGENTS.md, README.md, …)
    return path.startswith(_DOC_PREFIXES)


def select_doc_paths(tree: list[dict]) -> list[dict]:
    """Filter a GitHub git-tree response down to syncable doc blobs.

    Pure function — returns ``[{"path", "sha", "size"}]`` sorted by path.
    """
    docs = [
        {"path": t["path"], "sha": t["sha"], "size": int(t.get("size") or 0)}
        for t in tree
        if t.get("type") == "blob" and _is_doc_path(t.get("path", "")) and int(t.get("size") or 0) <= _MAX_FILE_BYTES
    ]
    docs.sort(key=lambda d: d["path"])
    return docs[:_MAX_FILES]


async def _fetch_tree() -> tuple[str, list[dict]]:
    url = f"https://api.github.com/repos/{REPO}/git/trees/{REF}?recursive=1"
    async with httpx.AsyncClient(timeout=30.0, headers=_GH_HEADERS) as gh:
        r = await gh.get(url)
        r.raise_for_status()
        payload = r.json()
    if payload.get("truncated"):
        logger.warning("[nc-docs] github tree truncated — manifest may be incomplete")
    return payload.get("sha", REF), payload.get("tree", [])


async def _fetch_blob(path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{quote(path)}"
    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": "socialauto-nextcloud-docs/1.0"}) as gh:
        r = await gh.get(url)
        r.raise_for_status()
        return r.content


async def sync_nextcloud_docs() -> dict:
    """Push changed repo docs to ``SocialAuto/docs/repo/`` on Nextcloud."""
    if not nextcloud_docs_enabled():
        return {"skipped": "nextcloud export disabled"}

    ref_sha, tree = await _fetch_tree()
    docs = select_doc_paths(tree)
    if not docs:
        return {"error": "empty doc manifest", "ref": ref_sha}

    uploaded: list[str] = []
    skipped = 0
    errors: list[dict] = []
    for doc in docs:
        path, sha = doc["path"], doc["sha"]
        try:
            if await _cursor(f"doc:{path}") == sha:
                skipped += 1
                continue
            data = await _fetch_blob(path)
            await nextcloud_export.upload_bytes(
                f"{_REMOTE_PREFIX}/{path}",
                data,
                mime_type="text/markdown; charset=utf-8",
            )
            await _set_cursor(f"doc:{path}", sha)
            uploaded.append(path)
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            errors.append({"path": path, "error": str(exc)[:200]})

    manifest = {
        "repo": REPO,
        "ref": REF,
        "ref_sha": ref_sha,
        "synced_at": datetime.now(UTC).isoformat(),
        "files": len(docs),
        "uploaded": len(uploaded),
        "skipped": skipped,
        "errors": errors,
    }
    await nextcloud_export.upload_bytes(
        f"{_REMOTE_PREFIX}/_manifest.json",
        json.dumps(manifest, indent=2).encode(),
        mime_type="application/json",
    )

    logger.info(
        "[nc-docs] %s@%s: %d files, %d uploaded, %d unchanged, %d errors",
        REPO,
        ref_sha[:7],
        len(docs),
        len(uploaded),
        skipped,
        len(errors),
    )
    return {"ref_sha": ref_sha, "files": len(docs), "uploaded": uploaded, "skipped": skipped, "errors": errors}
