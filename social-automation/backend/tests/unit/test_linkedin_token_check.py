"""Coverage for ``app.worker.tasks.linkedin_token_check`` (LinkedIn OAuth token health lane)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.tasks import linkedin_token_check as task


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
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _Result(self._rows)

    def update_patches(self):
        """meta_data patch dicts from UPDATE statements (Core update path)."""
        out = []
        for st in self.stmts:
            params = st.compile().params
            for v in params.values():
                if isinstance(v, dict) and "li_token_checked_at" in v:
                    out.append(v)
        return out

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
    def __init__(self, body=None, status=200):
        self._body = body or {}
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
    a.username = kw.get("username", "baltzakis.themis@gmail.com")
    a.account_id = kw.get("account_id", "a6JQwITmH3")
    a.access_token_enc = kw.get("access_token_enc", b"enc_tok")
    a.meta_data = kw.get("meta_data", {})
    a.token_expires_at = kw.get("token_expires_at", datetime.now(UTC) + timedelta(days=60))
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
    _wire(monkeypatch, [_acct()], _Client(_Resp({"sub": "abc"})))
    assert (await task._check())["healthy"] == 1


@pytest.mark.asyncio
async def test_401_alerts_and_flags(monkeypatch):
    a = _acct()
    db, alert = _wire(monkeypatch, [a], _Client(_Resp({"code": "AUTHENTICATION_FAILED"}, status=401)))
    stats = await task._check()
    assert stats["expired"] == 1
    patches = db.update_patches()
    assert patches and patches[0]["li_token_invalid"] is True
    assert "li_token_checked_at" in patches[0]
    alert.assert_awaited_once()
    assert db.committed


@pytest.mark.asyncio
async def test_403_alerts(monkeypatch):
    _, alert = _wire(monkeypatch, [_acct()], _Client(_Resp({}, status=403)))
    stats = await task._check()
    assert stats["expired"] == 1
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_expiring_soon_alerts(monkeypatch):
    a = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=3))
    db, alert = _wire(monkeypatch, [a], _Client(_Resp({"sub": "abc"})))
    stats = await task._check()
    assert stats["expiring"] == 1
    patches = db.update_patches()
    assert patches and patches[0]["li_token_invalid"] is False
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_expiry_counts_healthy(monkeypatch):
    a = _acct(token_expires_at=None)
    _wire(monkeypatch, [a], _Client(_Resp({"sub": "abc"})))
    assert (await task._check())["healthy"] == 1


@pytest.mark.asyncio
async def test_http_500_inconclusive_not_flagged(monkeypatch):
    a = _acct()
    db, alert = _wire(monkeypatch, [a], _Client(_Resp({}, status=500)))
    stats = await task._check()
    assert stats["inconclusive"] == 1 and stats["expired"] == 0
    patches = db.update_patches()
    assert all("li_token_invalid" not in p for p in patches)
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_error_inconclusive(monkeypatch):
    db, _ = _wire(monkeypatch, [_acct()], _Client(exc=RuntimeError("boom")))
    stats = await task._check()
    assert stats["scanned"] == 1 and stats["inconclusive"] == 1 and stats["healthy"] == 0
    assert db.committed


def test_wrapper_dispatches(monkeypatch):
    def _fake(coro):
        coro.close()
        return {"ok": 1}

    sent = MagicMock(side_effect=_fake)
    monkeypatch.setattr(task, "run_async", sent)
    assert task.check_linkedin_tokens() == {"ok": 1}
    sent.assert_called_once()
