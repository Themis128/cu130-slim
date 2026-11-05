"""Unit coverage for linkedin_engagement_score — CM per-post scoring."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_engagement_score as S


def test_score_weighting():
    s = S._score({"likeCount": 10, "commentCount": 5, "shareCount": 2, "impressionCount": 100})
    assert s == pytest.approx(10 + 10 + 6)
    # zero impressions → divide-by-1 guard
    assert S._score({"likeCount": 5, "impressionCount": 0}) == 500.0


@pytest.mark.asyncio
async def test_no_org_account_skips(monkeypatch):
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None)))
    )
    sess = SimpleNamespace(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock(return_value=False))
    monkeypatch.setattr(S, "task_session", lambda: sess)
    out = await S._score_linkedin_posts()
    assert out["skip"] == "no-linkedin-org-account"


@pytest.mark.asyncio
async def test_score_task_calls_run_async(monkeypatch):
    called = {}
    monkeypatch.setattr(S, "run_async", lambda c: called.setdefault("ran", True) or {"ok": 1})
    out = S.score_linkedin_posts.run()
    assert called["ran"] and out == {"ok": 1}
