"""Unit tests for app/worker/tasks/instagram_token_refresh.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.worker.tasks.instagram_token_refresh as I


@pytest.fixture(autouse=True)
def _common(monkeypatch):
    monkeypatch.setattr(I, "flag_modified", Mock())
    monkeypatch.setattr(I, "decrypt_token", lambda t: t.replace("enc:", ""))
    monkeypatch.setattr(I, "encrypt_token", lambda t: f"enc:{t}")
    monkeypatch.setattr(I, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(I, "session_heal_buttons", lambda: [])
    monkeypatch.setattr(I, "get_settings", lambda: SimpleNamespace(
        FACEBOOK_CLIENT_ID="cid", FACEBOOK_CLIENT_SECRET="csec"))


def _acct(**kw) -> SimpleNamespace:
    d = dict(
        id=uuid.uuid4(), platform="instagram", status="active",
        username="u1", account_id="ig1", access_token_enc="enc:IGQtok",
        meta_data={}, token_expires_at=None)
    d.update(kw)
    return SimpleNamespace(**d)


def _resp(status=200, body=None, text=""):
    r = SimpleNamespace(status_code=status, text=text)
    r.json = lambda: body if body is not None else {}
    return r


class _Http:
    """Routes by URL needle."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default or _resp(200, {"id": "1", "username": "u"})
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def get(self, url, **kw):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.default


class _CM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *e):
        return False


def _wire(monkeypatch, accounts, routes=None, default=None):
    """db.execute → accounts list; httpx → routed fake."""
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: accounts))),
        commit=AsyncMock())
    monkeypatch.setattr(I, "_worker_db", lambda: _CM(db))
    http = _Http(routes=routes, default=default)
    monkeypatch.setattr(I.httpx, "AsyncClient", lambda *a, **k: http)
    return db, http


# ── helpers ───────────────────────────────────────────────────────────


def test_token_kind_and_oauth_error():
    assert I._is_facebook_token("EAAtoken") is True
    assert I._is_facebook_token("IGQtoken") is False

    assert I._is_oauth_expired_error(_resp(
        400, {"error": {"code": 190}}))
    assert I._is_oauth_expired_error(_resp(
        400, {"error": {"type": "OAuthException"}}))
    assert not I._is_oauth_expired_error(_resp(400, {"error": {"code": 10}}))

    class _Bad:
        def json(self):
            raise ValueError("x")
    assert not I._is_oauth_expired_error(_Bad())


@pytest.mark.asyncio
async def test_mark_reconnect_required():
    db = SimpleNamespace(commit=AsyncMock())
    acct = _acct()
    await I._mark_reconnect_required(db, acct, {}, "reason x")
    assert acct.status == "expired"
    assert acct.meta_data["reconnect_required"] is True
    assert acct.meta_data["instagram_token_status"] == "expired"
    db.commit.assert_awaited_once()
    I.post_alert_to_slack.assert_awaited_once()
    # slack failure tolerated
    I.post_alert_to_slack.side_effect = RuntimeError("x")
    await I._mark_reconnect_required(db, acct, {}, "again")


# ── main task matrix ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_no_token_and_decrypt_fail(monkeypatch):
    # no access_token_enc → skipped
    _wire(monkeypatch, [_acct(access_token_enc=None)])
    out = await I._refresh_instagram_tokens_async()
    assert out["skipped_no_token"] == 1 and out["accounts_checked"] == 1

    # decrypt throws → invalid + meta marked
    monkeypatch.setattr(I, "decrypt_token",
                        Mock(side_effect=RuntimeError("bad")))
    acct = _acct()
    _wire(monkeypatch, [acct])
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_invalid"] == 1
    assert acct.meta_data["instagram_token_status"] == "invalid"


@pytest.mark.asyncio
async def test_refresh_validation_paths(monkeypatch):
    # /me 401 → reconnect_required + counted invalid
    acct = _acct()
    db, _ = _wire(monkeypatch, [acct],
                  routes={"/me": _resp(401, text="expired")})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_invalid"] == 1 and acct.status == "expired"

    # /me 200 with OAuthException body → still counts via _is_oauth_expired
    acct = _acct()
    _wire(monkeypatch, [acct], routes={"/me": _resp(
        400, {"error": {"code": 190}})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_invalid"] == 1

    # /me other status → falls through to expiry logic (no error counted)
    acct = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=30))
    _wire(monkeypatch, [acct], routes={"/me": _resp(500)})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1 and out["errors"] == 0

    # /me throws → errors++
    class _Boom:
        async def __aenter__(self):
            raise ConnectionError("down")
        async def __aexit__(self, *e):
            return False
    monkeypatch.setattr(I.httpx, "AsyncClient", lambda *a, **k: _Boom())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [_acct()]))),
        commit=AsyncMock())
    monkeypatch.setattr(I, "_worker_db", lambda: _CM(db))
    out = await I._refresh_instagram_tokens_async()
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_refresh_expiry_logic(monkeypatch):
    # far-out expiry → valid, no refresh call
    acct = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=30))
    db, http = _wire(monkeypatch, [acct])
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1 and out["tokens_refreshed"] == 0
    assert acct.meta_data["instagram_token_status"] == "valid"
    assert not any("refresh_access_token" in u for u in http.calls)

    # expiry ≤7 days → slack alert once + flag; >7 → flag cleared
    acct = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=3),
                 meta_data={})
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {"access_token": "NEW"})})
    out = await I._refresh_instagram_tokens_async()
    assert I.post_alert_to_slack.await_count == 1
    assert acct.meta_data["expiry_alert_sent"] is True
    assert out["tokens_refreshed"] == 1
    assert acct.access_token_enc == "enc:NEW"

    # legacy meta expiry key when column empty → backfills column
    acct = _acct(token_expires_at=None, meta_data={
        "instagram_token_expires_at":
            (datetime.now(UTC) + timedelta(days=30)).isoformat()})
    _wire(monkeypatch, [acct])
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1
    assert acct.token_expires_at is not None  # backfilled

    # FB token w/o expiry → debug_token gives expiry
    acct = _acct(platform="facebook", access_token_enc="enc:EAAfb",
                 meta_data={})
    _wire(monkeypatch, [acct], routes={
        "/debug_token": _resp(200, {"data": {
            "expires_at": int((datetime.now(UTC)
                               + timedelta(days=30)).timestamp())}})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1

    # debug_token expires_at=0 → never expires → valid
    acct = _acct(platform="facebook", access_token_enc="enc:EAAfb",
                 meta_data={})
    _wire(monkeypatch, [acct], routes={
        "/debug_token": _resp(200, {"data": {"expires_at": 0}})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1
    assert acct.meta_data["token_never_expires"] is True


@pytest.mark.asyncio
async def test_refresh_exchange_paths(monkeypatch):
    exp_soon = datetime.now(UTC) + timedelta(days=2)

    # IG refresh happy → token re-encrypted, expiry stored
    acct = _acct(token_expires_at=exp_soon, meta_data={
        "reconnect_required": True, "instagram_token_error": "old"})
    db, http = _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {
            "access_token": "NEWIG", "expires_in": 1000})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1
    assert acct.access_token_enc == "enc:NEWIG"
    assert "reconnect_required" not in acct.meta_data
    assert "instagram_token_error" not in acct.meta_data
    assert acct.meta_data["instagram_token_status"] == "refreshed"

    # FB exchange path — oauth/access_token URL used
    acct = _acct(platform="facebook", access_token_enc="enc:EAAfb",
                 token_expires_at=exp_soon)
    db, http = _wire(monkeypatch, [acct], routes={
        "/oauth/access_token": _resp(200, {"access_token": "NEWFB"})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1 and acct.access_token_enc == "enc:NEWFB"
    assert any("/oauth/access_token" in u for u in http.calls)

    # refresh non-200 with OAuth error → reconnect path
    acct = _acct(token_expires_at=exp_soon)
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(
            400, {"error": {"code": 190}}, text="dead")})
    out = await I._refresh_instagram_tokens_async()
    assert out["errors"] == 1 and acct.status == "expired"

    # refresh non-200 generic → refresh_failed meta
    acct = _acct(token_expires_at=exp_soon)
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(500, text="oops")})
    out = await I._refresh_instagram_tokens_async()
    assert acct.meta_data["instagram_token_status"] == "refresh_failed"

    # refresh 200 but no token → errors
    acct = _acct(token_expires_at=exp_soon)
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {})})
    out = await I._refresh_instagram_tokens_async()
    assert out["errors"] == 1

    # refresh request throws → errors
    acct = _acct(token_expires_at=exp_soon)
    class _Boom:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *e):
            return False
        async def get(self, url, **kw):
            if "refresh" in url:
                raise RuntimeError("net down")
            return _resp(200, {"id": "1"})
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [acct]))),
        commit=AsyncMock())
    monkeypatch.setattr(I, "_worker_db", lambda: _CM(db))
    monkeypatch.setattr(I.httpx, "AsyncClient", lambda *a, **k: _Boom())
    out = await I._refresh_instagram_tokens_async()
    assert out["errors"] == 1


# ── _run_async + task wrapper ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_async_running_loop_thread():
    # a loop is already running → coro executes in a worker thread
    out = I._run_async(_coro_val(41))
    assert out == 42

    async def _boom():
        raise ValueError("inner")
    with pytest.raises(ValueError, match="inner"):
        I._run_async(_boom())


async def _coro_val(n):
    return n + 1


def test_run_async_not_running_and_no_loop(monkeypatch):
    # existing loop but not running → falls through to run_async
    monkeypatch.setattr(I, "run_async", lambda c: c.close() or "via-run_async")
    monkeypatch.setattr(I.asyncio, "get_event_loop",
                        lambda: SimpleNamespace(is_running=lambda: False))

    async def _c():
        return "x"
    assert I._run_async(_c()) == "via-run_async"

    # get_event_loop raises RuntimeError → run_async fallback
    def _raise():
        raise RuntimeError("no loop")
    monkeypatch.setattr(I.asyncio, "get_event_loop", _raise)

    async def _c2():
        return "y"
    assert I._run_async(_c2()) == "via-run_async"


def test_refresh_task_wrapper(monkeypatch):
    seen = {}
    def _fake_run(c):
        seen["c"] = c
        return {"ok": 1}
    monkeypatch.setattr(I, "_run_async", _fake_run)

    async def _impl():
        return {"ok": 1}
    monkeypatch.setattr(I, "_refresh_instagram_tokens_async", _impl)
    assert I.refresh_instagram_tokens() == {"ok": 1}
    seen["c"].close()


# ── remaining expiry branches ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_expiry_edge_branches(monkeypatch):
    # naive token_expires_at → tz backfilled (line 222)
    acct = _acct(token_expires_at=datetime.now(UTC).replace(tzinfo=None)
                 + timedelta(days=30))
    _wire(monkeypatch, [acct])
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_valid"] == 1

    # unparseable legacy meta expiry → ignored, refresh proceeds (228-229)
    acct = _acct(meta_data={"instagram_token_expires_at": "not-a-date"})
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {"access_token": "NEW"})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1

    # debug_token response raises on .json() → warning, refresh proceeds
    bad = SimpleNamespace(status_code=200, text="x")
    def _badjson():
        raise RuntimeError("bad json")
    bad.json = _badjson
    acct = _acct(platform="facebook", access_token_enc="enc:EAAfb",
                 meta_data={})
    _wire(monkeypatch, [acct], routes={
        "/debug_token": bad,
        "/oauth/access_token": _resp(200, {"access_token": "NEWFB"})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1

    # slack expiry alert raises → non-fatal, refresh still happens (287-288)
    I.post_alert_to_slack.side_effect = RuntimeError("slack down")
    acct = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=3))
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {"access_token": "NEW"})})
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1
    assert "expiry_alert_sent" not in acct.meta_data  # flag only set on success
