"""Unit tests for the Nextcloud docs-sync manifest filter + sync path."""

import pytest
import respx

import app.services.nextcloud_docs as nd
from app.services.nextcloud_docs import _is_doc_path, select_doc_paths


def _blob(path: str, size: int = 100) -> dict:
    return {"type": "blob", "path": path, "sha": "abc123", "size": size}


def test_root_markdown_included() -> None:
    assert _is_doc_path("AGENTS.md")
    assert _is_doc_path("README.md")


def test_doc_prefixes_included() -> None:
    assert _is_doc_path("docs/architecture.md")
    assert _is_doc_path("deploy/omv-workspace/README.md")
    assert _is_doc_path(".devin/BACKLOG.md")
    assert _is_doc_path(".devin/skills/foo-ops/SKILL.md")
    assert _is_doc_path(".devin/rules/cloudless-social-stack.md")
    assert _is_doc_path(".cursor/skills/foo-ops/SKILL.md")
    assert _is_doc_path("social-automation/backend/NOTES.md")


def test_non_doc_paths_excluded() -> None:
    assert not _is_doc_path("social-automation/backend/app/main.py")
    assert not _is_doc_path("docs/guide.txt")
    assert not _is_doc_path("src/components/Button.tsx")
    # code dirs outside the doc prefixes never leak in even as .md
    assert not _is_doc_path("scripts/deploy.md")
    assert not _is_doc_path("src/components/README.md")


def test_select_doc_paths_filters_and_sorts() -> None:
    tree = [
        _blob("README.md"),
        _blob("docs/zeta.md"),
        _blob("docs/alpha.md"),
        _blob("app/main.py"),
        {"type": "tree", "path": "docs", "sha": "t1"},
        _blob("docs/huge.md", size=10_000_000),
    ]
    docs = select_doc_paths(tree)
    assert [d["path"] for d in docs] == ["README.md", "docs/alpha.md", "docs/zeta.md"]
    assert all(d["sha"] == "abc123" for d in docs)


def test_select_doc_paths_empty_tree() -> None:
    assert select_doc_paths([]) == []
    assert select_doc_paths([{"type": "commit", "path": "x"}]) == []


# ── async sync path (all I/O mocked) ─────────────────────────────────────────


def _tree(*paths: str) -> list[dict]:
    return [{"type": "blob", "path": p, "sha": f"sha-{p}", "size": 10} for p in paths]


class _Cursor:
    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.mark.asyncio
async def test_sync_disabled_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nd, "nextcloud_docs_enabled", lambda: False)
    out = await nd.sync_nextcloud_docs()
    assert out == {"skipped": "nextcloud export disabled"}


@pytest.mark.asyncio
async def test_sync_uploads_only_changed_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nd, "nextcloud_docs_enabled", lambda: True)
    docs = _tree("AGENTS.md", "docs/a.md")
    monkeypatch.setattr(nd, "_fetch_tree", lambda: _coro(("refsha", docs)))
    monkeypatch.setattr(nd, "_fetch_blob", lambda p: _coro(f"content-{p}".encode()))

    uploads: list[tuple] = []

    async def _upload(remote_path, data, mime_type="application/octet-stream"):
        uploads.append((remote_path, data))
        return remote_path

    monkeypatch.setattr(nd.nextcloud_export, "upload_bytes", _upload)
    # AGENTS.md already synced at same sha → skipped; docs/a.md uploads
    monkeypatch.setattr(nd, "_cursor", lambda key: _coro("sha-AGENTS.md" if key == "doc:AGENTS.md" else None))
    set_keys: list[str] = []
    monkeypatch.setattr(nd, "_set_cursor", lambda k, v: _coro(set_keys.append(k)))

    out = await nd.sync_nextcloud_docs()
    assert out["files"] == 2
    assert out["skipped"] == 1
    assert out["uploaded"] == ["docs/a.md"]
    file_uploads = [u for u in uploads if u[0] != "docs/repo/_manifest.json"]
    assert file_uploads == [("docs/repo/docs/a.md", b"content-docs/a.md")]
    assert set_keys == ["doc:docs/a.md"]
    # manifest always written
    assert uploads[-1][0] == "docs/repo/_manifest.json"


@pytest.mark.asyncio
async def test_sync_per_file_error_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nd, "nextcloud_docs_enabled", lambda: True)
    docs = _tree("a.md", "b.md")
    monkeypatch.setattr(nd, "_fetch_tree", lambda: _coro(("refsha", docs)))

    async def _blob(path):
        if path == "a.md":
            raise RuntimeError("github 500")
        return b"ok"

    monkeypatch.setattr(nd, "_fetch_blob", _blob)
    uploads: list[str] = []

    async def _upload(remote_path, data, mime_type="application/octet-stream"):
        uploads.append(remote_path)
        return remote_path

    monkeypatch.setattr(nd.nextcloud_export, "upload_bytes", _upload)
    monkeypatch.setattr(nd, "_cursor", lambda key: _coro(None))
    monkeypatch.setattr(nd, "_set_cursor", lambda k, v: _coro(None))

    out = await nd.sync_nextcloud_docs()
    assert out["uploaded"] == ["b.md"]
    assert len(out["errors"]) == 1
    assert out["errors"][0]["path"] == "a.md"


@pytest.mark.asyncio
async def test_sync_empty_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nd, "nextcloud_docs_enabled", lambda: True)
    monkeypatch.setattr(nd, "_fetch_tree", lambda: _coro(("refsha", [{"type": "tree", "path": "x"}])))
    out = await nd.sync_nextcloud_docs()
    assert out == {"error": "empty doc manifest", "ref": "refsha"}


async def _coro(value):
    return value


@pytest.mark.asyncio
async def test_fetch_tree_and_blob_via_respx() -> None:
    with respx.mock(assert_all_called=False) as mock:
        tree_payload = {
            "sha": "abc",
            "tree": [{"type": "blob", "path": "README.md", "sha": "s1", "size": 5}],
            "truncated": False,
        }
        mock.get(url__regex=r"api\.github\.com/repos/Themis128/cu130-slim/git/trees/master.*").mock(return_value=respx.MockResponse(200, json=tree_payload))
        mock.get(url__regex=r"raw\.githubusercontent\.com/Themis128/cu130-slim/master/README\.md").mock(return_value=respx.MockResponse(200, content=b"# hi"))
        ref, tree = await nd._fetch_tree()
        assert ref == "abc"
        assert tree[0]["path"] == "README.md"
        assert await nd._fetch_blob("README.md") == b"# hi"
