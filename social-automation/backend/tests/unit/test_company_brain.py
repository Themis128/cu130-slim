"""Unit tests for the company-brain memory layer.

Exercises the real service end-to-end; only the true external boundaries
are stubbed: the embedding call (DMR/CF) and the HTTP transport shared by
Chroma + Nextcloud Notes.
"""
import json
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.models.content import PostStatus
from app.services import company_brain as cb


@pytest.fixture()
def brain_on(monkeypatch):
    monkeypatch.setattr(cb.settings, "COMPANY_BRAIN_ENABLED", True)
    monkeypatch.setattr(cb.settings, "CHROMA_URL", "http://chroma:8000")
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_APP_PASSWORD", "x")
    monkeypatch.setattr(cb, "_get_embedding", lambda *a, **k: _fake_embed())
    return cb.settings


async def _fake_embed():
    return [0.1] * 8


def _wire_http(monkeypatch, handler):
    """Route every httpx.AsyncClient in the module through a MockTransport."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: real(transport=transport)
    )


class _PostsResult:
    """Mimics the scalar+unique result the posts query consumes."""

    def __init__(self, posts):
        self._posts = posts

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return list(self._posts)


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeDB:
    """execute() returns canned results in call order:
    posts → followers@end → followers@start."""

    def __init__(self, posts, followers_now, followers_then):
        self._results = [_PostsResult(posts), _RowsResult(followers_now), _RowsResult(followers_then)]

    async def execute(self, _q):
        return self._results.pop(0)


def _post(status, platforms, failure=None, when=None):
    targets = [
        SimpleNamespace(social_account=SimpleNamespace(platform=p))
        for p in platforms
    ]
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        failure_reason=failure,
        published_at=when if status == PostStatus.PUBLISHED else None,
        failed_at=when if status == PostStatus.FAILED else None,
        targets=targets,
    )


@pytest.mark.asyncio
async def test_journal_text_renders_real_signals(brain_on):
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    signals = {
        "published": {"linkedin": 2, "instagram": 1},
        "failed": {"tiktok": ["url_ownership_unverified"]},
        "followers": {"linkedin": (853, 850), "tiktok": (4, 4)},
        "post_count": 4,
    }
    text = cb._journal_text(team.name, date(2026, 10, 18), signals)
    assert "linkedin: 2 post(s)" in text
    assert "tiktok: 1 failed — url_ownership_unverified" in text
    assert "linkedin: 853 (+3 vs prior day)" in text


@pytest.mark.asyncio
async def test_write_daily_journal_stores_memory_and_note(brain_on, monkeypatch):
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    day = date(2026, 10, 18)
    when = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    posts = [
        _post(PostStatus.PUBLISHED, ["linkedin"], when=when),
        _post(PostStatus.FAILED, ["instagram"], failure="media not available", when=when),
    ]
    db = _FakeDB(
        posts,
        followers_now=[("linkedin", 853)],
        followers_then=[("linkedin", 850)],
    )

    chroma_adds: list[dict] = []
    notes: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/collections") or "/collections/" in path:
            if request.method == "GET":
                return httpx.Response(200, json={"id": "col-1"})
            if path.endswith("/add"):
                chroma_adds.append(json.loads(request.content))
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"id": "col-1"})
        if "/apps/notes/" in path:
            if request.method == "GET":
                return httpx.Response(200, json=[])
            notes.append((request.method, json.loads(request.content)))
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(404, json={"path": path})

    _wire_http(monkeypatch, handler)
    out = await cb.write_daily_journal(db, team, day=day)

    assert out["memory"] is True and out["note"] is True
    assert chroma_adds[0]["ids"] == ["journal-2026-10-18"]
    doc = chroma_adds[0]["documents"][0]
    assert "instagram: 1 failed — media not available" in doc
    assert notes[0][0] == "POST" and notes[0][1]["title"] == "Company Brain — 2026-10-18"


@pytest.mark.asyncio
async def test_write_daily_journal_updates_existing_note(brain_on, monkeypatch):
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    db = _FakeDB([], followers_now=[], followers_then=[])
    puts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/collections" in path:
            if path.endswith("/add"):
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"id": "col-1"})
        if "/apps/notes/" in path:
            if request.method == "GET":
                return httpx.Response(
                    200, json=[{"id": 42, "title": "Company Brain — 2026-10-18"}]
                )
            if request.method == "PUT":
                puts.append(json.loads(request.content))
                return httpx.Response(200, json={"id": 42})
        return httpx.Response(404, json={"path": path})

    _wire_http(monkeypatch, handler)
    out = await cb.write_daily_journal(db, team, day=date(2026, 10, 18))
    assert out["note"] is True
    assert len(puts) == 1  # updated, not duplicated


@pytest.mark.asyncio
async def test_recall_returns_documents(brain_on, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/query"):
            return httpx.Response(200, json={"documents": [["journal A", "journal B"]]})
        return httpx.Response(200, json={"id": "col-1"})

    _wire_http(monkeypatch, handler)
    out = await cb.recall(uuid.uuid4(), "what failed yesterday")
    assert out == ["journal A", "journal B"]


@pytest.mark.asyncio
async def test_disabled_brain_no_ops(brain_on, monkeypatch):
    monkeypatch.setattr(cb.settings, "COMPANY_BRAIN_ENABLED", False)
    assert await cb.recall(uuid.uuid4(), "anything") == []
    assert await cb.remember(uuid.uuid4(), "d1", "text") is False
