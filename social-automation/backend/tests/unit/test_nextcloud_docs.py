"""Unit tests for the Nextcloud docs-sync manifest filter (pure functions)."""

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
