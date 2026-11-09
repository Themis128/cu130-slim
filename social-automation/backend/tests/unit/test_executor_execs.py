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


@pytest.mark.asyncio
async def test_devapp_health_dedups_within_24h(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    slack = AsyncMock()
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", slack)
    action = {"type": "devapp_health", "platform": "twitter", "username": "me", "reason": "quota exhausted"}

    db = _db(executed=None)
    out = await M._exec_devapp_health(db, "t1", action)
    assert out["alerted"] == "twitter/@me"
    slack.assert_awaited_once()

    db2 = _db(executed=MagicMock())  # prior event exists → dedup hit
    out2 = await M._exec_devapp_health(db2, "t1", action)
    assert out2["skipped"].startswith("already")
    assert slack.await_count == 1  # no second alert
