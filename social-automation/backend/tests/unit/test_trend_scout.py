"""Tests for app/services/trend_scout.py — trend discovery sources."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trend_scout as ts


class _FakeHTTP:
    def __init__(self, *, get=None, get_exc=None):
        self._get = get
        self._get_exc = get_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, **kw):
        if self._get_exc:
            raise self._get_exc
        return self._get


class _Resp:
    def __init__(self, code=200, data=None):
        self.status_code = code
        self._data = data or {}

    def json(self):
        return self._data


def _patch(monkeypatch, client):
    monkeypatch.setattr(ts.httpx, "AsyncClient", lambda *a, **kw: client)


# ── twitter ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_trends_no_token():
    assert await ts.get_twitter_trends(None) == []


@pytest.mark.asyncio
async def test_twitter_trends_ok(monkeypatch):
    data = {"trends": [{"name": "#AI", "url": "http://x", "tweet_volume": 1234}, {"name": "#B"}]}
    _patch(monkeypatch, _FakeHTTP(get=_Resp(200, data)))
    out = await ts.get_twitter_trends("tok")
    assert out[0] == {"name": "#AI", "url": "http://x", "tweet_volume": 1234}
    assert out[1]["tweet_volume"] == 0


@pytest.mark.asyncio
async def test_twitter_trends_failures(monkeypatch):
    _patch(monkeypatch, _FakeHTTP(get=_Resp(429)))
    assert await ts.get_twitter_trends("tok") == []
    _patch(monkeypatch, _FakeHTTP(get_exc=RuntimeError("x")))
    assert await ts.get_twitter_trends("tok") == []


# ── reddit ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reddit_hot_ok(monkeypatch):
    data = {
        "data": {
            "children": [
                {"data": {"title": "T", "permalink": "/r/m/1", "score": 5,
                          "num_comments": 3, "subreddit": "marketing"}},
            ]
        }
    }
    _patch(monkeypatch, _FakeHTTP(get=_Resp(200, data)))
    out = await ts.get_reddit_hot("marketing", 5)
    assert out[0]["title"] == "T"
    assert out[0]["url"] == "https://reddit.com/r/m/1"
    assert out[0]["score"] == 5


@pytest.mark.asyncio
async def test_reddit_hot_failures(monkeypatch):
    _patch(monkeypatch, _FakeHTTP(get=_Resp(404)))
    assert await ts.get_reddit_hot() == []
    _patch(monkeypatch, _FakeHTTP(get_exc=RuntimeError("x")))
    assert await ts.get_reddit_hot() == []


# ── own top posts ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_top_performing_posts():
    post = SimpleNamespace(content_text="hello world")
    snap = SimpleNamespace(impressions=100, likes=3, comments=2, shares=1, platform="linkedin")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(post, snap)]))
    out = await ts.get_top_performing_posts(db, uuid.uuid4())
    assert out == [{
        "title": "hello world",
        "impressions": 100,
        "engagement": 6,
        "platform": "linkedin",
    }]


@pytest.mark.asyncio
async def test_top_performing_posts_nulls_and_no_platform_attr():
    # content None -> "", metrics None -> 0, no platform attr -> "unknown"
    post = SimpleNamespace(content_text=None)
    snap = SimpleNamespace(impressions=None, likes=None, comments=None, shares=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(post, snap)]))
    out = await ts.get_top_performing_posts(db, uuid.uuid4())
    assert out == [{"title": "", "impressions": 0, "engagement": 0, "platform": "unknown"}]


@pytest.mark.asyncio
async def test_top_performing_posts_error():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    assert await ts.get_top_performing_posts(db, uuid.uuid4()) == []


# ── scout aggregation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scout_trends(monkeypatch):
    monkeypatch.setattr(ts, "get_twitter_trends", AsyncMock(return_value=[{"name": "t"}]))
    monkeypatch.setattr(
        ts,
        "get_reddit_hot",
        AsyncMock(side_effect=[["a"], RuntimeError("x"), ["b"]]),
    )
    monkeypatch.setattr(
        ts, "get_top_performing_posts", AsyncMock(return_value=[{"title": "p"}])
    )
    out = await ts.scout_trends(AsyncMock(), uuid.uuid4(), "tok")
    assert out["twitter_trends"] == [{"name": "t"}]
    assert out["reddit_hot"] == ["a", "b"]  # exception result filtered
    assert out["top_posts"] == [{"title": "p"}]


@pytest.mark.asyncio
async def test_scout_trends_default_subreddits(monkeypatch):
    calls = []

    async def fake_reddit(sub, limit=10):
        calls.append(sub)
        return []

    monkeypatch.setattr(ts, "get_twitter_trends", AsyncMock(return_value=[]))
    monkeypatch.setattr(ts, "get_reddit_hot", fake_reddit)
    monkeypatch.setattr(ts, "get_top_performing_posts", AsyncMock(return_value=[]))
    out = await ts.scout_trends(AsyncMock(), uuid.uuid4())
    assert calls == ["marketing", "socialmedia", "contentmarketing"]
    assert out["reddit_hot"] == []
