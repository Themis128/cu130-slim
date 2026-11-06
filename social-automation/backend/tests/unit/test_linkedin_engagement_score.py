"""Unit coverage for linkedin_engagement_score â€” CM per-post scoring."""
"""Unit coverage for linkedin_engagement_score — CM per-post scoring."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.tasks import linkedin_engagement_score as S


class _AsyncCM:
    """Minimal async context manager — SimpleNamespace can't be one."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False

import app.worker.tasks.linkedin_engagement_score as S

def test_score_weighting():
    s = S._score({"likeCount": 10, "commentCount": 5, "shareCount": 2, "impressionCount": 100})
    assert s == pytest.approx(10 + 10 + 6)
    # zero impressions â†’ divide-by-1 guard
    # zero impressions → divide-by-1 guard
    assert S._score({"likeCount": 5, "impressionCount": 0}) == 500.0


@pytest.mark.asyncio
async def test_no_org_account_skips(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))))
    monkeypatch.setattr(S, "task_session", lambda: _AsyncCM(db))
    sess = SimpleNamespace(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock(return_value=False))
    monkeypatch.setattr(S, "task_session", lambda: sess)
    out = await S._score_linkedin_posts()
    assert out["skip"] == "no-linkedin-org-account"


@pytest.mark.asyncio
async def test_score_task_calls_run_async(monkeypatch):
    called = {}

    def _ra(c):
        called["ran"] = True
        return {"ok": 1}

    monkeypatch.setattr(S, "run_async", _ra)
    monkeypatch.setattr(S, "run_async", lambda c: called.setdefault("ran", True) or {"ok": 1})
    out = S.score_linkedin_posts.run()
    assert called["ran"] and out == {"ok": 1}
