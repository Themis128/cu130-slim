"""Unit tests for the Bluesky credential-rotation sweep task."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        if self._account is not None and self._recent is None or (self._account is not None and self._recent is not None):
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
    with patch.object(bc, "task_session", lambda: _SessionCtx(db)), patch("app.services.slack_notifications.send_telegram_alert", new=AsyncMock()):
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "no recent 401" in out["reason"]


@pytest.mark.asyncio
async def test_sweep_401_without_main_password_alerts():
    db = _make_db(_account(), True)
    alert = AsyncMock(return_value=True)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.dict("os.environ", {}, clear=False),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
    ):
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
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.dict("os.environ", {"BSKY_MAIN_PASSWORD": "main-pw"}),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
        patch(
            "app.services.bluesky_api.rotate_app_password",
            new=AsyncMock(return_value={"rotated": True, "app_password_name": "x"}),
        ),
    ):
        out = await bc._sweep()
    assert out["rotated"] is True
    alert.assert_awaited_once()
    assert db.commits == 0  # commit happens inside rotate_app_password


# --- _stored_password_dead live-probe branches -------------------------


def _probe_account(**kw):
    base = {
        "id": "a1",
        "username": "h.bsky.social",
        "meta_data": {},
        "access_token_enc": b"enc",
    }
    base.update(kw)
    return type("A", (), base)()


@pytest.mark.asyncio
async def test_probe_undecryptable_token_is_inconclusive():
    with patch("app.core.security.decrypt_token", side_effect=ValueError("bad")):
        assert await bc._stored_password_dead(_probe_account()) is None


@pytest.mark.asyncio
async def test_probe_session_ok_means_alive():
    client = AsyncMock()
    client.create_session = AsyncMock(return_value=None)
    with (
        patch("app.core.security.decrypt_token", return_value="pw"),
        patch("app.services.bluesky_api.BlueskyClient", return_value=client),
    ):
        assert await bc._stored_password_dead(_probe_account()) is False


@pytest.mark.asyncio
async def test_probe_401_means_dead():
    from app.services.bluesky_api import BlueskyAPIError

    client = AsyncMock()
    client.create_session = AsyncMock(
        side_effect=BlueskyAPIError(401, "AuthenticationRequired", "u")
    )
    with (
        patch("app.core.security.decrypt_token", return_value="pw"),
        patch("app.services.bluesky_api.BlueskyClient", return_value=client),
    ):
        assert await bc._stored_password_dead(_probe_account()) is True


@pytest.mark.asyncio
async def test_probe_429_is_inconclusive():
    from app.services.bluesky_api import BlueskyAPIError

    client = AsyncMock()
    client.create_session = AsyncMock(
        side_effect=BlueskyAPIError(429, "rate limited", "u")
    )
    with (
        patch("app.core.security.decrypt_token", return_value="pw"),
        patch("app.services.bluesky_api.BlueskyClient", return_value=client),
    ):
        assert await bc._stored_password_dead(_probe_account()) is None


@pytest.mark.asyncio
async def test_probe_generic_error_is_inconclusive():
    client = AsyncMock()
    client.create_session = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch("app.core.security.decrypt_token", return_value="pw"),
        patch("app.services.bluesky_api.BlueskyClient", return_value=client),
    ):
        assert await bc._stored_password_dead(_probe_account()) is None


@pytest.mark.asyncio
async def test_probe_uses_meta_data_pds_url():
    client = AsyncMock()
    client.create_session = AsyncMock(return_value=None)
    cls = MagicMock(return_value=client)
    acc = _probe_account(meta_data={"pds_url": "https://pds.example.com"})
    with (
        patch("app.core.security.decrypt_token", return_value="pw"),
        patch("app.services.bluesky_api.BlueskyClient", cls),
    ):
        await bc._stored_password_dead(acc)
    _, kwargs = cls.call_args
    assert kwargs["pds_url"] == "https://pds.example.com"


# --- _sweep probe-driven branches --------------------------------------


@pytest.mark.asyncio
async def test_sweep_probe_inconclusive():
    db = _make_db(_account(), False)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.object(bc, "_stored_password_dead", new=AsyncMock(return_value=None)),
        patch("app.services.slack_notifications.send_telegram_alert", new=AsyncMock()),
    ):
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "inconclusive" in out["reason"]


@pytest.mark.asyncio
async def test_sweep_probe_alive_noop():
    db = _make_db(_account(), False)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.object(bc, "_stored_password_dead", new=AsyncMock(return_value=False)),
        patch("app.services.slack_notifications.send_telegram_alert", new=AsyncMock()),
    ):
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "still valid" in out["reason"]


@pytest.mark.asyncio
async def test_sweep_probe_dead_without_main_password_alerts():
    db = _make_db(_account(), False)
    alert = AsyncMock(return_value=True)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.object(bc, "_stored_password_dead", new=AsyncMock(return_value=True)),
        patch.dict("os.environ", {}, clear=False),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
    ):
        import os

        os.environ.pop("BSKY_MAIN_PASSWORD", None)
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "BSKY_MAIN_PASSWORD" in out["reason"]
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_rotation_429_is_silent():
    from app.services.bluesky_api import BlueskyAPIError

    db = _make_db(_account(), True)
    alert = AsyncMock(return_value=True)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.dict("os.environ", {"BSKY_MAIN_PASSWORD": "main-pw"}),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
        patch(
            "app.services.bluesky_api.rotate_app_password",
            new=AsyncMock(side_effect=BlueskyAPIError(429, "slow down", "u")),
        ),
    ):
        out = await bc._sweep()
    assert out["rotated"] is False
    assert "error" in out
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_rotation_500_alerts():
    from app.services.bluesky_api import BlueskyAPIError

    db = _make_db(_account(), True)
    alert = AsyncMock(return_value=True)
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.dict("os.environ", {"BSKY_MAIN_PASSWORD": "main-pw"}),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
        patch(
            "app.services.bluesky_api.rotate_app_password",
            new=AsyncMock(side_effect=BlueskyAPIError(500, "oops", "u")),
        ),
    ):
        out = await bc._sweep()
    assert out["rotated"] is False
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_main_password_from_settings():
    import types

    db = _make_db(_account(), True)
    alert = AsyncMock(return_value=True)
    fake_settings = types.SimpleNamespace(BSKY_MAIN_PASSWORD="settings-pw")
    with (
        patch.object(bc, "task_session", lambda: _SessionCtx(db)),
        patch.dict("os.environ", {}, clear=False),
        patch("app.core.config.settings", fake_settings),
        patch("app.services.slack_notifications.send_telegram_alert", new=alert),
        patch(
            "app.services.bluesky_api.rotate_app_password",
            new=AsyncMock(return_value={"rotated": True, "app_password_name": "x"}),
        ),
    ):
        import os

        os.environ.pop("BSKY_MAIN_PASSWORD", None)
        out = await bc._sweep()
    assert out["rotated"] is True
