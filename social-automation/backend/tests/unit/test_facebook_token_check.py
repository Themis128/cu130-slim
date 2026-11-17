"""Coverage for ``app.worker.tasks.facebook_token_check`` (FB Graph token health lane)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.tasks import facebook_token_check as task


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def execute(self, _stmt):
        return _Result(self._rows)

    async def commit(self):
        self.committed = True


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return {"data": self._data}


class _RespBody:
    """Raw-body response (threads /me + error envelopes have no 'data' key)."""

    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        return self._body


class _Client:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        if self._exc:
            raise self._exc
        return self._resp


def _acct(**kw):
    a = MagicMock()
    a.id = kw.get("id", 1)
    a.platform = kw.get("platform", "facebook")
    a.username = kw.get("username", "page")
    a.access_token_enc = kw.get("access_token_enc", b"enc_tok")
    a.meta_data = kw.get("meta_data", {"app_access_token": "app_tok"})
    return a


def _wire(monkeypatch, rows, client):
    db = _Db(rows)
    monkeypatch.setattr(task, "task_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(task, "decrypt_token", lambda t: t)
    monkeypatch.setattr(task.httpx, "AsyncClient", lambda **k: client)
    alert = AsyncMock()
    monkeypatch.setattr(task, "send_telegram_alert", alert)
    return db, alert


@pytest.mark.asyncio
async def test_no_accounts(monkeypatch):
    db, _ = _wire(monkeypatch, [], _Client())
    assert await task._check() == {"scanned": 0, "healthy": 0, "expiring": 0, "expired": 0, "inconclusive": 0}
    assert db.committed


@pytest.mark.asyncio
async def test_missing_token_skipped(monkeypatch):
    rows = [_acct(access_token_enc=b""), _acct(meta_data={})]
    _wire(monkeypatch, rows, _Client())
    stats = await task._check()
    assert stats["scanned"] == 2 and stats["healthy"] == 0


@pytest.mark.asyncio
async def test_healthy(monkeypatch):
    _wire(monkeypatch, [_acct()], _Client(_Resp({"is_valid": True, "expires_at": 0})))
    assert (await task._check())["healthy"] == 1


@pytest.mark.asyncio
async def test_invalid_alerts(monkeypatch):
    a = _acct()
    db, alert = _wire(monkeypatch, [a], _Client(_Resp({"is_valid": False})))
    stats = await task._check()
    assert stats["expired"] == 1
    assert a.meta_data["fb_token_invalid"] is True
    assert "fb_token_checked_at" in a.meta_data
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_expiring_soon_alerts(monkeypatch):
    import datetime as dt

    exp = int(dt.datetime.now(dt.UTC).timestamp()) + 3 * 86400
    a = _acct()
    _, alert = _wire(monkeypatch, [a], _Client(_Resp({"is_valid": True, "expires_at": exp})))
    stats = await task._check()
    assert stats["expiring"] == 1
    assert a.meta_data["fb_token_invalid"] is False
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_error_continues(monkeypatch):
    _wire(monkeypatch, [_acct()], _Client(exc=RuntimeError("boom")))
    stats = await task._check()
    assert stats["scanned"] == 1 and stats["healthy"] == 0


def test_wrapper_dispatches(monkeypatch):
    def _fake(coro):
        coro.close()
        return {"ok": 1}

    sent = MagicMock(side_effect=_fake)
    monkeypatch.setattr(task, "run_async", sent)
    assert task.check_facebook_tokens() == {"ok": 1}
    sent.assert_called_once()


@pytest.mark.asyncio
async def test_threads_probe_healthy(monkeypatch):
    a = _acct(platform="threads")
    a.token_expires_at = None
    _wire(monkeypatch, [a], _Client(_RespBody({"id": "1784"})))
    stats = await task._check()
    assert stats["healthy"] == 1 and stats["inconclusive"] == 0


@pytest.mark.asyncio
async def test_threads_probe_dead_code_alerts(monkeypatch):
    a = _acct(platform="threads")
    a.token_expires_at = None
    _, alert = _wire(monkeypatch, [a], _Client(_RespBody({"error": {"code": 190}}, status=400)))
    stats = await task._check()
    assert stats["expired"] == 1
    assert a.meta_data["fb_token_invalid"] is True
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_inconclusive_transient_not_flagged(monkeypatch):
    a = _acct()
    _, alert = _wire(monkeypatch, [a], _Client(_RespBody({"error": {"code": 2}}, status=500)))
    stats = await task._check()
    assert stats["inconclusive"] == 1 and stats["expired"] == 0
    assert "fb_token_invalid" not in a.meta_data
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_threads_expiry_falls_back_to_stored(monkeypatch):
    import datetime as dt

    a = _acct(platform="threads")
    a.token_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=3)
    _, alert = _wire(monkeypatch, [a], _Client(_RespBody({"id": "1784"})))
    stats = await task._check()
    assert stats["expiring"] == 1
    alert.assert_awaited_once()
