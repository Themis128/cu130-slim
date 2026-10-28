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
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(transport=transport))


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
    targets = [SimpleNamespace(social_account=SimpleNamespace(platform=p)) for p in platforms]
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
                return httpx.Response(200, json=[{"id": 42, "title": "Company Brain — 2026-10-18"}])
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


@pytest.mark.asyncio
async def test_remember_empty_text_and_no_embedding(brain_on, monkeypatch):
    assert await cb.remember(uuid.uuid4(), "d1", "   ") is False
    monkeypatch.setattr(cb, "_get_embedding", lambda *a, **k: _no_embed())
    assert await cb.remember(uuid.uuid4(), "d1", "real text") is False


async def _no_embed():
    return None


@pytest.mark.asyncio
async def test_remember_missing_collection_and_transport_error(brain_on, monkeypatch):
    # _wire_http captures the real AsyncClient once — a second patch in the
    # same test would chain the previous lambda and keep the old transport.
    # One handler with a mode switch avoids that.
    state = {"mode": "missing"}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["mode"] == "missing":
            return httpx.Response(404, json={"missing": True})
        if state["mode"] == "boom" and request.url.path.endswith("/add"):
            raise httpx.ConnectError("chroma down")
        return httpx.Response(200, json={"id": "col-1"})

    _wire_http(monkeypatch, handler)
    assert await cb.remember(uuid.uuid4(), "d1", "text") is False
    state["mode"] = "boom"
    assert await cb.remember(uuid.uuid4(), "d1", "text") is False


@pytest.mark.asyncio
async def test_recall_no_embedding_returns_empty(brain_on, monkeypatch):
    monkeypatch.setattr(cb, "_get_embedding", lambda *a, **k: _no_embed())
    assert await cb.recall(uuid.uuid4(), "q") == []


@pytest.mark.asyncio
async def test_recall_cited_returns_hits_with_provenance(brain_on, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "documents": [["journal A", "journal B"]],
                    "metadatas": [[{"kind": "daily-journal", "day": "2026-10-21"}, {}]],
                    "distances": [[0.12, 0.44]],
                },
            )
        return httpx.Response(200, json={"id": "col-1"})

    _wire_http(monkeypatch, handler)
    out = await cb.recall_cited(uuid.uuid4(), "what failed yesterday")
    assert out["gap"] is None
    assert out["hits"][0]["day"] == "2026-10-21"
    assert out["hits"][0]["distance"] == 0.12
    assert out["hits"][1]["kind"] is None  # missing metadata → None, not a crash


@pytest.mark.asyncio
async def test_recall_cited_gap_states(brain_on, monkeypatch):
    # disabled → named gap
    monkeypatch.setattr(cb.settings, "COMPANY_BRAIN_ENABLED", False)
    out = await cb.recall_cited(uuid.uuid4(), "q")
    assert out == {"hits": [], "gap": "company brain disabled or empty query"}
    monkeypatch.setattr(cb.settings, "COMPANY_BRAIN_ENABLED", True)
    assert (await cb.recall_cited(uuid.uuid4(), ""))["gap"] is not None

    # no embedding → named gap
    monkeypatch.setattr(cb, "_get_embedding", lambda *a, **k: _no_embed())
    assert (await cb.recall_cited(uuid.uuid4(), "q"))["gap"] == "embedding unavailable"


@pytest.mark.asyncio
async def test_recall_cited_transport_gaps(brain_on, monkeypatch):
    state = {"mode": "missing"}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["mode"] == "missing":
            return httpx.Response(404, json={})
        if request.url.path.endswith("/query"):
            if state["mode"] == "non200":
                return httpx.Response(500, json={"err": "q"})
            if state["mode"] == "empty":
                return httpx.Response(200, json={"documents": [[]], "metadatas": [[]], "distances": [[]]})
            raise httpx.ConnectError("chroma down")
        return httpx.Response(200, json={"id": "col-1"})

    _wire_http(monkeypatch, handler)
    assert (await cb.recall_cited(uuid.uuid4(), "q"))["gap"] == "no brain collection — journal has never run"
    state["mode"] = "non200"
    assert (await cb.recall_cited(uuid.uuid4(), "q"))["gap"] == "chroma query HTTP 500"
    state["mode"] = "empty"
    assert (await cb.recall_cited(uuid.uuid4(), "q"))["gap"] == "no memories match — coverage gap"
    state["mode"] = "boom"
    assert "recall error" in (await cb.recall_cited(uuid.uuid4(), "q"))["gap"]


@pytest.mark.asyncio
async def test_recall_missing_collection_non200_and_error(brain_on, monkeypatch):
    state = {"mode": "missing"}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["mode"] == "missing":
            return httpx.Response(404, json={})
        if request.url.path.endswith("/query"):
            if state["mode"] == "non200":
                return httpx.Response(500, json={"err": "q"})
            raise httpx.ConnectError("chroma down")
        return httpx.Response(200, json={"id": "col-1"})

    _wire_http(monkeypatch, handler)
    assert await cb.recall(uuid.uuid4(), "q") == []
    state["mode"] = "non200"
    assert await cb.recall(uuid.uuid4(), "q") == []
    state["mode"] = "boom"
    assert await cb.recall(uuid.uuid4(), "q") == []


def test_journal_text_skips_none_follower_snapshot():
    signals = {
        "published": {},
        "failed": {},
        "followers": {"linkedin": (None, 850), "tiktok": (4, None)},
        "post_count": 0,
    }
    text = cb._journal_text("T", date(2026, 10, 18), signals)
    assert "linkedin" not in text.split("## Followers")[1]
    assert "tiktok: 4 (n/a vs prior day)" in text


@pytest.mark.asyncio
async def test_nextcloud_note_dav_fallback_and_not_configured(brain_on, monkeypatch):
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_BASE_URL", "")
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_DAV_URL", "https://dav.test/remote.php/dav/files/t/")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if "/apps/notes/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"id": 1})
        return httpx.Response(404, json={})

    _wire_http(monkeypatch, handler)
    assert await cb._write_nextcloud_note("T", "body") is True
    assert seen and seen[0] == "dav.test"

    monkeypatch.setattr(cb.settings, "NEXTCLOUD_DAV_URL", "")
    assert await cb._write_nextcloud_note("T", "body") is False

    monkeypatch.setattr(cb.settings, "NEXTCLOUD_BASE_URL", "https://cloud.test")
    monkeypatch.setattr(cb.settings, "NEXTCLOUD_USERNAME", "   ")
    assert await cb._write_nextcloud_note("T", "body") is False


@pytest.mark.asyncio
async def test_nextcloud_note_transport_error_returns_false(brain_on, monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nc down")

    _wire_http(monkeypatch, boom)
    assert await cb._write_nextcloud_note("T", "body") is False
