"""engagement_ingest: keyword search on X -> EngagementCandidate rows."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, mock_open

import pytest

import app.worker._async as A
from app.services import brand_monitoring as BM
from app.worker.tasks import engagement as E


def _db(existing):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = existing
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(A, "run_async", lambda c: asyncio.run(c))
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(E.os.path, "exists", lambda p: False)
    return monkeypatch


def test_no_bearer(env):
    assert E.engagement_ingest.run() == {"ingested": 0, "reason": "no bearer"}


def test_bearer_from_dotenv_and_ingest_with_limit(env):
    env.setattr(E.os.path, "exists", lambda p: p == "/app/.env")
    env.setattr("builtins.open", mock_open(read_data="# c\nX_BEARER_TOKEN=tok\nJUNK\n"))
    db = _db(["dup"])
    env.setattr(E, "task_session", lambda: db)
    calls = []

    async def search(kw, bearer, max_results=10):
        calls.append((kw, bearer))
        return [
            {"url": "https://x.com/a/status/dup"},
            {"url": ""},
            {"url": "https://x.com/a/status/1", "author": "a", "content": "hi", "mentioned_at": "2026-10-01T10:00:00Z", "engagement": 3},
            {"url": "https://x.com/a/status/1"},
            {"url": f"https://x.com/b/status/{kw}", "author": "b"},
        ]

    env.setattr(BM, "search_twitter_mentions", search)
    out = E.engagement_ingest.run(limit=3)
    assert out == {"ingested": 3}
    assert calls[0][1] == "tok" and len(calls) == 2
    rows = [c.args[0] for c in db.add.call_args_list]
    assert rows[0].post_id == "1" and rows[0].posted_at.year == 2026
    assert rows[0].meta_data["engagement"] == 3
    assert rows[1].posted_at is not None
    db.commit.assert_awaited_once()


def test_ingest_all_keywords_under_limit(env):
    env.setenv("TWITTER_BEARER_TOKEN", "b")
    db = _db([])
    env.setattr(E, "task_session", lambda: db)
    env.setattr(BM, "search_twitter_mentions", AsyncMock(return_value=[]))
    assert E.engagement_ingest.run() == {"ingested": 0}
    assert BM.search_twitter_mentions.await_count == len(E.ENGAGE_KEYWORDS)
