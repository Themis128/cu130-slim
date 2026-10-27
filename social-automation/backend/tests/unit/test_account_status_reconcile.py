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
    with patch.object(R.httpx, "AsyncClient", lambda **k: _Client(_Resp(200))):
        assert await R._probe("facebook", "123", "tok", {}) == "active"


@pytest.mark.asyncio
async def test_probe_meta_401_revoked():
    with patch.object(R.httpx, "AsyncClient", lambda **k: _Client(_Resp(401))):
        assert await R._probe("instagram", "123", "tok", {}) == "revoked"


@pytest.mark.asyncio
async def test_probe_meta_500_error():
    with patch.object(R.httpx, "AsyncClient", lambda **k: _Client(_Resp(500))):
        assert await R._probe("threads", "123", "tok", {}) == "error"


@pytest.mark.asyncio
async def test_probe_tiktok_error_code_maps_revoked():
    with patch.object(R.httpx, "AsyncClient",
                      lambda **k: _Client(_Resp(200, {"error": {"code": "access_token_invalid"}}))):
        assert await R._probe("tiktok", "x", "tok", {}) == "revoked"


@pytest.mark.asyncio
async def test_probe_tiktok_ok():
    with patch.object(R.httpx, "AsyncClient",
                      lambda **k: _Client(_Resp(200, {"error": {"code": "ok"}}))):
        assert await R._probe("tiktok", "x", "tok", {}) == "active"


@pytest.mark.asyncio
async def test_probe_unknown_platform_returns_error():
    with patch.object(R.httpx, "AsyncClient", lambda **k: _Client(_Resp(200))):
        assert await R._probe("mastodon", "x", "tok", {}) == "error"


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

    with patch.object(R, "task_session", lambda: _Sess()), \
         patch.object(R, "post_alert_to_slack", new=AsyncMock()) as alert, \
         patch.object(R, "_probe", new=AsyncMock(return_value="active")) as probe:
        counts = await R._reconcile()
    probe.assert_not_awaited()  # expired-by-date never hits the network
    assert acc.status == "expired"
    assert counts["expired"] == 1 and counts["drifted"] == 1
    alert.assert_awaited_once()


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

    with patch.object(R, "task_session", lambda: _Sess()), \
         patch.object(R, "decrypt_token", return_value="tok"), \
         patch.object(R, "_probe", new=AsyncMock(return_value="active")):
        counts = await R._reconcile()
    assert "last_verified_at" in acc.meta_data
    assert counts["active"] == 1 and counts["drifted"] == 0
