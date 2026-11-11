"""Unit tests for the Bluesky credential-rotation sweep task."""

from unittest.mock import AsyncMock, patch

import pytest

from app.worker.tasks import bluesky_credential as bc


class _Result:
    def __init__(self, obj=None):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj


class _DB:
    def __init__(self, account, recent_401):
        self._account = account
        self._recent = recent_401
        self.commits = 0

    async def execute(self, _stmt):
        if self._account is not None and self._recent is None or (
            self._account is not None and self._recent is not None
        ):
            pass
        return _Result(self._account)


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _make_db(account, has_401):
    calls = {"n": 0}

    class DB:
        commits = 0

        async def execute(self, _stmt):
            calls["n"] += 1
            # first execute → account scalars().first(); second → recent_401 .first()
            if calls["n"] == 1:
                return _Result(account)
            return _Result(object() if has_401 else None)

        async def commit(self):
            self.commits += 1

    return DB()


def _account():
    return type(
        "A",
        (),
        {"id": "acc1", "platform": "bluesky", "username": "h.bsky.social", "meta_data": {}, "access_token_enc": b"x"},
    )()


@pytest.mark.asyncio
async def test_sweep_no_account():
    db = _make_db(None, False)
    with patch.object(bc, "task_session", lambda: _SessionCtx(db)):
        out = await bc._sweep()
    assert out == {"skipped": "no bluesky account"}


@pytest.mark.asyncio
async def test_sweep_no_401_noop():
    db = _make_db(_account(), False)
    with patch.object(bc, "task_session", lambda: _SessionCtx(db)), patch(
        "app.services.slack_notifications.send_telegram_alert", new=AsyncMock()
    ):
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "no recent 401" in out["reason"]


@pytest.mark.asyncio
async def test_sweep_401_without_main_password_alerts():
    db = _make_db(_account(), True)
    alert = AsyncMock(return_value=True)
    with patch.object(bc, "task_session", lambda: _SessionCtx(db)), patch.dict(
        "os.environ", {}, clear=False
    ), patch("app.services.slack_notifications.send_telegram_alert", new=alert):
        import os
        os.environ.pop("BSKY_MAIN_PASSWORD", None)
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "BSKY_MAIN_PASSWORD" in out["reason"]
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_401_rotates_and_alerts():
    acc = _account()
    db = _make_db(acc, True)
    alert = AsyncMock(return_value=True)
    with patch.object(bc, "task_session", lambda: _SessionCtx(db)), patch.dict(
        "os.environ", {"BSKY_MAIN_PASSWORD": "main-pw"}
    ), patch("app.services.slack_notifications.send_telegram_alert", new=alert), patch(
        "app.services.bluesky_api.rotate_app_password",
        new=AsyncMock(return_value={"rotated": True, "app_password_name": "x"}),
    ):
        out = await bc._sweep()
    assert out["rotated"] is True
    alert.assert_awaited_once()
    assert db.commits == 0  # commit happens inside rotate_app_password
