"""Auth-terminal revive path in publish_retry._sweep (PR #2575 coverage)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Sess:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _mk_db(targets):
    res = MagicMock()
    res.scalars.return_value.all.return_value = targets
    res2 = MagicMock()
    res2.scalar_one.return_value = 0  # no active queue row
    res2.scalars.return_value.first.return_value = MagicMock(status="failed")

    calls = {"n": 0}

    async def _exec(*a, **k):
        calls["n"] += 1
        return res if calls["n"] == 1 else res2

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_exec)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_auth_terminal_revives_once_for_active_account(monkeypatch):
    from app.worker.tasks import publish_retry as P

    now = datetime.now(UTC)
    post = MagicMock(meta_data={})
    account = MagicMock(status="active")
    tgt = MagicMock(
        error_message="session has been invalidated because the user changed their password",
        error_class=None,
        updated_at=now - timedelta(hours=2),
        status="failed",
        id="t1",
        target_id="t1",
        post_id="p1",
        social_account_id="a1",
        post=post,
        social_account=account,
    )
    db = _mk_db([tgt])
    monkeypatch.setattr(P, "task_session", lambda: _Sess(db))

    out = await P._sweep()
    assert out["retried"] == 1
    assert tgt.status == "pending"
    assert tgt.error_message is None
    assert post.meta_data["auth_resurrected"] == ["p1:a1"]


@pytest.mark.asyncio
async def test_auth_terminal_skips_when_account_still_dead(monkeypatch):
    from app.worker.tasks import publish_retry as P

    now = datetime.now(UTC)
    tgt = MagicMock(
        error_message="invalid_grant",
        error_class=None,
        updated_at=now - timedelta(hours=2),
        status="failed",
        id="t1",
        target_id="t1",
        post_id="p1",
        social_account_id="a1",
        post=MagicMock(meta_data={}),
        social_account=MagicMock(status="expired"),
    )
    db = _mk_db([tgt])
    monkeypatch.setattr(P, "task_session", lambda: _Sess(db))

    out = await P._sweep()
    assert out["retried"] == 0
    assert tgt.status == "failed"


@pytest.mark.asyncio
async def test_auth_terminal_not_re_revived(monkeypatch):
    from app.worker.tasks import publish_retry as P

    now = datetime.now(UTC)
    post = MagicMock(meta_data={"auth_resurrected": ["p1:a1"]})
    tgt = MagicMock(
        error_message="401 Unauthorized",
        error_class=None,
        updated_at=now - timedelta(hours=2),
        status="failed",
        id="t1",
        target_id="t1",
        post_id="p1",
        social_account_id="a1",
        post=post,
        social_account=MagicMock(status="active"),
    )
    db = _mk_db([tgt])
    monkeypatch.setattr(P, "task_session", lambda: _Sess(db))

    out = await P._sweep()
    assert out["retried"] == 0
    assert tgt.status == "failed"
    assert post.meta_data["auth_resurrected"] == ["p1:a1"]


@pytest.mark.asyncio
async def test_content_terminal_still_skips_even_with_active_account(monkeypatch):
    from app.worker.tasks import publish_retry as P

    now = datetime.now(UTC)
    tgt = MagicMock(
        error_message="Only photo or video can be accepted as media type",
        error_class=None,
        updated_at=now - timedelta(hours=2),
        status="failed",
        id="t1",
        target_id="t1",
        post_id="p1",
        social_account_id="a1",
        post=MagicMock(meta_data={}),
        social_account=MagicMock(status="active"),
    )
    db = _mk_db([tgt])
    monkeypatch.setattr(P, "task_session", lambda: _Sess(db))

    out = await P._sweep()
    assert out["retried"] == 0
    assert tgt.status == "failed"
