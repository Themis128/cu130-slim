"""Unit tests for the social_accounts platform-truth reconcile task."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.tasks import account_status_reconcile as R


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._p = payload or {}

    def json(self):
        return self._p


class _Client:
    """Async-context httpx stand-in returning a canned response."""

    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self.resp

    async def post(self, *a, **k):
        return self.resp


@pytest.mark.asyncio
async def test_probe_meta_200_active():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(200))):
        assert await R._probe("facebook", "123", "tok", {}) == "active"


@pytest.mark.asyncio
async def test_probe_meta_401_revoked():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(401))):
        assert await R._probe("instagram", "123", "tok", {}) == "revoked"


@pytest.mark.asyncio
async def test_probe_meta_500_error():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(500))):
        assert await R._probe("threads", "123", "tok", {}) == "inconclusive"


@pytest.mark.asyncio
async def test_probe_tiktok_error_code_maps_revoked():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(200, {"error": {"code": "access_token_invalid"}}))):
        assert await R._probe("tiktok", "x", "tok", {}) == "revoked"


@pytest.mark.asyncio
async def test_probe_tiktok_ok():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(200, {"error": {"code": "ok"}}))):
        assert await R._probe("tiktok", "x", "tok", {}) == "active"


@pytest.mark.asyncio
async def test_probe_unknown_platform_returns_error():
    with patch.object(R, "SafeAsyncClient", lambda **k: _Client(_Resp(200))):
        assert await R._probe("mastodon", "x", "tok", {}) == "unprobed"


@pytest.mark.asyncio
async def test_reconcile_marks_expired_without_probe():
    from datetime import UTC, datetime, timedelta

    acc = MagicMock()
    acc.token_expires_at = datetime.now(UTC) - timedelta(days=1)
    acc.access_token_enc = b"x"
    acc.status = "active"
    acc.meta_data = {}
    acc.platform = "twitter"
    acc.username = "u"
    acc.account_id = "1"

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [acc])))
    fake_db.commit = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(R, "task_session", lambda: _Sess()),
        patch.object(R, "post_alert_to_slack", new=AsyncMock()) as alert,
        patch.object(R, "_probe", new=AsyncMock(return_value="active")) as probe,
    ):
        counts = await R._reconcile()
    probe.assert_not_awaited()  # expired-by-date never hits the network
    assert acc.status == "expired"
    assert counts["expired"] == 1 and counts["drifted"] == 1
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_web_lane_instagram_is_unprobed():
    """IG rows with a live web_rupload session must not be Graph-probed —
    the token is revoked by design and the sweep would flip-flop status."""
    acc = MagicMock()
    acc.token_expires_at = None
    acc.access_token_enc = b"x"
    acc.status = "active"
    acc.meta_data = {
        "private_api_session_id": "enc-session",
        "private_api_csrf_token": "enc-csrf",
        "private_api_ds_user_id": "12345",
    }
    acc.platform = "instagram"
    acc.username = "cloudless.gr"
    acc.account_id = "17841436821573754"

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [acc])))
    fake_db.commit = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(R, "task_session", lambda: _Sess()),
        patch.object(R, "decrypt_token", return_value="tok"),
        patch.object(R, "_probe", new=AsyncMock(return_value="revoked")) as probe,
    ):
        counts = await R._reconcile()
    probe.assert_not_awaited()
    assert acc.status == "active"
    assert counts["revoked"] == 0 and counts["drifted"] == 0


@pytest.mark.asyncio
async def test_instagram_without_web_lane_still_probes():
    acc = MagicMock()
    acc.token_expires_at = None
    acc.access_token_enc = b"x"
    acc.status = "active"
    acc.meta_data = {"private_api_session_id": "enc-session"}  # incomplete set
    acc.platform = "instagram"
    acc.username = "ig-no-lane"
    acc.account_id = "1"

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [acc])))
    fake_db.commit = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    probe = AsyncMock(return_value="active")
    with (
        patch.object(R, "task_session", lambda: _Sess()),
        patch.object(R, "decrypt_token", return_value="tok"),
        patch.object(R, "_probe", new=probe),
    ):
        await R._reconcile()
    probe.assert_awaited_once()


def test_web_lane_helper_matrix():
    full = {
        "private_api_session_id": "s",
        "private_api_csrf_token": "c",
        "private_api_ds_user_id": "u",
    }
    assert R._has_web_publish_lane("instagram", full) is True
    assert R._has_web_publish_lane("instagram", {}) is False
    assert R._has_web_publish_lane("instagram", None) is False
    assert R._has_web_publish_lane("facebook", full) is False
    assert R._has_web_publish_lane("instagram", {k: v for k, v in full.items() if k != "private_api_ds_user_id"}) is False


@pytest.mark.asyncio
async def test_reconcile_stamps_last_verified_at():
    acc = MagicMock()
    acc.token_expires_at = None
    acc.access_token_enc = b"x"
    acc.status = "active"
    acc.meta_data = {}
    acc.platform = "linkedin"
    acc.username = "u"
    acc.account_id = "1"

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [acc])))
    fake_db.commit = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(R, "task_session", lambda: _Sess()),
        patch.object(R, "decrypt_token", return_value="tok"),
        patch.object(R, "_probe", new=AsyncMock(return_value="active")),
    ):
        counts = await R._reconcile()
    assert "last_verified_at" in acc.meta_data
    assert counts["active"] == 1 and counts["drifted"] == 0
