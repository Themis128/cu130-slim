"""Coverage: datalake_executor audit-escalation ping + factory."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(executed=None, acc=None):
    db = MagicMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = executed
    res2 = MagicMock()
    res2.scalar_one.return_value = acc
    db.execute = AsyncMock(side_effect=[res1, res2])
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_audit_escalation_pings(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    acc = MagicMock(meta_data={"bot_token_enc": "e"}, access_token_enc=b"t")
    db = _db(executed=None, acc=acc)
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok")
    tg = MagicMock()
    tg.send_message = AsyncMock()
    monkeypatch.setattr(M, "TelegramAPIClient", MagicMock(return_value=tg))
    out = await M._exec_audit_escalation(db, "t1", {"emails": ["a@x", "b@y"]})
    assert out["pinged"] == 2
    tg.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_escalation_dedup(monkeypatch):
    from app.worker.tasks import datalake_executor as M

    db = _db(executed=MagicMock())
    out = await M._exec_audit_escalation(db, "t1", {"emails": ["a@x"]})
    assert out["skipped"].startswith("already")


def test_factory_custom():
    from app.worker.tasks import datalake_executor as M

    f = object()
    assert M._factory(custom=f) is f


def test_factory_default():
    from app.worker.tasks import datalake_executor as M

    out = M._factory()
    assert out is not None
