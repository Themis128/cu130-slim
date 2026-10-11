"""Tests for app/worker/tasks/tiktok_inbox_reconcile.py — MEDIA_UPLOAD draft reconcile."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.tiktok_inbox_reconcile as rec


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _DB:
    def __init__(self, results):
        self._results = list(results)
        self._i = 0
        self.committed = False

    async def execute(self, q):
        r = self._results[self._i]
        self._i += 1
        return r

    async def commit(self):
        self.committed = True


class _DBCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _account(**kw):
    base = dict(
        id=uuid.uuid4(), platform="tiktok", status="active",
        access_token_enc=b"enc", username="cloudless",
        meta_data={"open_id": "oid"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _target(account, *, post_id="pub_1", status="published", published_at=None,
            error_message=None, post=None, **kw):
    base = dict(
        id=uuid.uuid4(), platform_post_id=post_id, platform_url=None,
        status=status, error_message=error_message,
        published_at=published_at, created_at=datetime.now(UTC) - timedelta(hours=1),
        social_account=account, post=post,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _wire(monkeypatch, db, targets, *, publish_ok=True, check=None):
    monkeypatch.setattr(rec, "_worker_db", lambda: _DBCM(db))
    monkeypatch.setattr(rec, "decrypt_token", lambda b: "tok")
    import app.services.tiktok_api as tt

    monkeypatch.setattr(tt, "is_tiktok_publish_id", lambda pid: publish_ok)
    client = SimpleNamespace(
        check_publish_status=check or AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}})
    )
    monkeypatch.setattr(tt, "TikTokAPIClient", lambda **kw: client)
    return client


@pytest.mark.asyncio
async def test_reconcile_no_targets(monkeypatch):
    db = _DB([_Res([])])
    _wire(monkeypatch, db, [])
    out = await rec._reconcile_async()
    assert out == {"checked": 0, "published": 0, "failed": 0, "pending": 0, "errors": []}
    assert db.committed


@pytest.mark.asyncio
async def test_reconcile_publish_complete(monkeypatch):
    post = SimpleNamespace(platform_specific={}, published_at=datetime.now(UTC))
    acc = _account()
    t = _target(acc, post=post)
    check = AsyncMock(return_value={
        "data": {"status": "PUBLISH_COMPLETE",
                 "publicaly_available_post_id": ["vid_9"]},
    })
    db = _DB([_Res([t])])
    _wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    assert out["published"] == 1 and out["checked"] == 1
    assert t.platform_post_id == "vid_9"
    assert t.platform_url == "https://www.tiktok.com/@cloudless/video/vid_9"
    assert t.error_message is None
    assert post.platform_specific["tiktok"]["publicaly_available_post_id"] == "vid_9"
    assert post.platform_specific["tiktok"]["inbox_status"] == "PUBLISH_COMPLETE"


@pytest.mark.asyncio
async def test_reconcile_publish_complete_no_ids_and_no_username(monkeypatch):
    acc = _account(username=None)
    t = _target(acc, platform_url="keep", post=None)
    check = AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}})
    db = _DB([_Res([t])])
    _wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    # no video id -> nothing upgraded
    assert out["published"] == 0
    assert t.platform_post_id == "pub_1"
    assert t.platform_url == "keep"


@pytest.mark.asyncio
async def test_reconcile_failed_status(monkeypatch):
    acc = _account()
    t = _target(acc)
    check = AsyncMock(return_value={"data": {"status": "FAILED", "fail_reason": "copyright"}})
    db = _DB([_Res([t])])
    _wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    assert out["failed"] == 1
    assert t.status == "failed"
    assert "copyright" in t.error_message


@pytest.mark.asyncio
async def test_reconcile_inbox_pending_fresh_vs_stale(monkeypatch):
    acc = _account()
    fresh = _target(acc, published_at=datetime.now(UTC))
    stale = _target(acc, published_at=datetime.now(UTC) - timedelta(hours=30))
    already = _target(
        acc, published_at=datetime.now(UTC) - timedelta(hours=30),
        error_message="already flagged",
    )
    check = AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}})
    db = _DB([_Res([fresh, stale, already])])
    _wire(monkeypatch, db, None, check=check)
    out = await rec._reconcile_async()
    assert out["pending"] == 3
    assert fresh.error_message is None
    assert stale.error_message == rec.INBOX_PENDING_MESSAGE
    assert already.error_message == "already flagged"


@pytest.mark.asyncio
async def test_reconcile_exception_per_target(monkeypatch):
    acc = _account()
    t = _target(acc)
    check = AsyncMock(side_effect=RuntimeError("net down"))
    db = _DB([_Res([t])])
    _wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    assert out["checked"] == 1
    assert len(out["errors"]) == 1 and "net down" in out["errors"][0]


@pytest.mark.asyncio
async def test_reconcile_skips_no_account_and_non_publish_ids(monkeypatch):
    orphan = _target(None, post_id="pub_orphan")
    acc = _account()
    normal = _target(acc, post_id="regular_video_id")
    db = _DB([_Res([orphan, normal])])
    client = _wire(monkeypatch, db, None)
    import app.services.tiktok_api as tt
    # only the orphan's id counts as a publish_id → 'normal' filtered out,
    # orphan reaches the loop but has no account → skipped silently
    monkeypatch.setattr(tt, "is_tiktok_publish_id", lambda pid: pid == "pub_orphan")
    out = await rec._reconcile_async()
    assert out["checked"] == 0
    client.check_publish_status.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_unknown_status_noop(monkeypatch):
    acc = _account()
    t = _target(acc)
    check = AsyncMock(return_value={"data": {"status": "PROCESSING"}})
    db = _DB([_Res([t])])
    _wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    assert out["checked"] == 1
    assert t.status == "published"  # untouched


def test_reconcile_tiktok_inbox_wrapper(monkeypatch):
    def fake_run(coro):
        coro.close()  # discard without running — wrapper only delegates
        return "ran"

    monkeypatch.setattr(rec, "run_async", fake_run)
    assert rec.reconcile_tiktok_inbox() == "ran"
