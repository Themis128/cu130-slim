"""Coverage: datalake_executor exec paths — content_topic, dedup."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(executed=None, user_id=None):
    db = MagicMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = executed
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = user_id
    db.execute = AsyncMock(side_effect=[res1, res2])
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_content_topic_skips_dedup(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    db = _db(executed=MagicMock())
    out = await M._exec_content_topic(db, "t1", {"topic": "pain"})
    assert out["skipped"].startswith("already")


@pytest.mark.asyncio
async def test_content_topic_creates_post(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    db = _db(executed=None, user_id="u1")
    added = []
    db.add.side_effect = lambda o: added.append(o)
    action = {"topic": "cost pain", "platform": "facebook", "reason": "r"}
    out = await M._exec_content_topic(db, "t1", action)
    assert out["platform"] == "facebook"
    assert added and added[0].meta_data["topic"] == "cost pain"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_content_topic_defaults_platform(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    db = _db(executed=None, user_id="u1")
    out = await M._exec_content_topic(db, "t1", {"topic": "x"})
    assert out["platform"] == "linkedin"
