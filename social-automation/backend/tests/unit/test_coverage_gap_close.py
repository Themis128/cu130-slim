"""Coverage close-out: intravox digest push, nurture sweep wrapper, publish retry sweep."""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest


class _R:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p


class _C:
    def __init__(self, post=_R(201, {"uniqueId": "u1"}), get=_R(200, {"layout": {}}), put=_R(200)):
        self._post, self._get, self._put = post, get, put

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return self._post

    async def get(self, *a, **k):
        return self._get

    async def put(self, *a, **k):
        return self._put


@pytest.mark.asyncio
async def test_intravox_digest_push_happy(monkeypatch):
    from app.worker.tasks import skool_watch as S
    monkeypatch.setenv("NEXTCLOUD_DAV_URL", "https://nc.example/remote.php/dav")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "u")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "p")
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C())
    await S._push_intravox_digest([{"t": "post one", "h": "/p/1"}])


@pytest.mark.asyncio
async def test_intravox_digest_push_no_creds_returns():
    from app.worker.tasks import skool_watch as S
    for k in ("NEXTCLOUD_BASE_URL", "NEXTCLOUD_DAV_URL", "NEXTCLOUD_USERNAME", "NEXTCLOUD_APP_PASSWORD"):
        os.environ.pop(k, None)
    await S._push_intravox_digest([{"t": "x", "h": "/p"}])  # early return, no call


class _Sess:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def test_nurture_sweep_skips_without_team(monkeypatch):
    from app.worker.tasks import nurture
    s = MagicMock()
    s.CLOUDLESS_WEB_ANALYTICS_TEAM_ID = ""
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    out = nurture.run_nurture_sweep()
    assert out == {"skipped": "no public-lead team configured"}


@pytest.mark.asyncio
async def test_publish_retry_resets_and_skips(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.worker.tasks import publish_retry as P

    now = datetime.now(UTC)
    non_retry = MagicMock(error_message="media format rejected by platform",
                          updated_at=now - timedelta(hours=2), status="failed")
    fresh = MagicMock(error_message="timeout", updated_at=now, status="failed")
    stale = MagicMock(error_message="timeout", updated_at=now - timedelta(hours=2),
                      status="failed", id="t1", target_id="t1", meta_data={})

    res = MagicMock()
    res.scalars.return_value.all.return_value = [non_retry, fresh, stale]
    q = MagicMock(status="queued")
    res2 = MagicMock()
    res2.scalars.return_value.first.return_value = q
    calls = {"n": 0}

    async def _exec(*a, **k):
        calls["n"] += 1
        return res if calls["n"] == 1 else res2

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_exec)
    db.commit = AsyncMock()
    monkeypatch.setattr(P, "task_session", lambda: _Sess(db))
    out = await P._sweep()
    assert stale.status == "pending"
    assert non_retry.status == "failed" and fresh.status == "failed"
    assert out["retried"] >= 1
