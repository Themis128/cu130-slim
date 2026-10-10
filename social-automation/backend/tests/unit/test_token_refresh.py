"""Unit tests for app/worker/tasks/token_refresh.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.token_refresh as TR


def _http(status=200, body=None):
    resp = SimpleNamespace(status_code=status, json=lambda: body or {})
    calls = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls.append((url, kw))
            return resp

    return _C(), calls


def test_get_oauth_client():
    for p in ("linkedin", "twitter", "facebook", "instagram",
              "threads", "tiktok"):
        assert TR._get_oauth_client(p) is not None
    assert TR._get_oauth_client("whatsapp") is None
    assert TR._get_oauth_client("viber") is None


@pytest.mark.asyncio
async def test_meta_self_refresh(monkeypatch):
    # instagram business login — happy + error
    http, calls = _http(body={"access_token": "new"})
    monkeypatch.setattr(TR.httpx, "AsyncClient", lambda **kw: http)
    out = await TR._refresh_instagram_business_token("old")
    assert out["access_token"] == "new"
    assert "graph.instagram.com" in calls[0][0]
    assert calls[0][1]["params"]["grant_type"] == "ig_refresh_token"

    http, _ = _http(status=400, body={"error": {"message": "expired"}})
    monkeypatch.setattr(TR.httpx, "AsyncClient", lambda **kw: http)
    with pytest.raises(RuntimeError, match="expired"):
        await TR._refresh_instagram_business_token("old")

    # threads — happy + fallback error message
    http, calls = _http(body={"access_token": "t2"})
    monkeypatch.setattr(TR.httpx, "AsyncClient", lambda **kw: http)
    out = await TR._refresh_threads_token("old")
    assert out["access_token"] == "t2"
    assert "graph.threads.net" in calls[0][0]

    http, _ = _http(status=500, body={})
    monkeypatch.setattr(TR.httpx, "AsyncClient", lambda **kw: http)
    with pytest.raises(RuntimeError, match="th_refresh_token HTTP 500"):
        await TR._refresh_threads_token("old")


def test_skip_for_recent_update():
    now = datetime.now(UTC)

    # no expiry set → never skip (must refresh)
    assert TR._skip_for_recent_update(None, now, now) is False
    # expires within the run window → never skip (dead soon)
    assert TR._skip_for_recent_update(
        now + timedelta(minutes=1), now, now) is False
    # token survives next run but no updated_at → no skip
    assert TR._skip_for_recent_update(
        now + timedelta(days=5), None, now) is False
    # survives + recent update → skip
    assert TR._skip_for_recent_update(
        now + timedelta(days=5), now - timedelta(minutes=1), now) is True
    # survives + old update → don't skip
    assert TR._skip_for_recent_update(
        now + timedelta(days=5), now - timedelta(hours=2), now) is False
    # naive updated_at handled
    assert TR._skip_for_recent_update(
        now + timedelta(days=5), now.replace(tzinfo=None), now) is True


def _account(**kw):
    base = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="linkedin",
        username="u", account_id="x", status="active",
        meta_data={}, access_token_enc="enc_a",
        refresh_token_enc="enc_r",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        updated_at=None, scopes=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _db_with(accounts):
    class _DB:
        commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: accounts))

    return _DB


@pytest.mark.asyncio
async def test_refresh_async_guards(monkeypatch):
    monkeypatch.setattr(TR, "decrypt_token", lambda t: f"dec-{t}")
    monkeypatch.setattr(TR, "encrypt_token", lambda t: f"enc-{t}")

    # empty → zeroed summary
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([])())
    out = await TR._refresh_expiring_tokens_async()
    assert out == {"checked": 0, "refreshed": 0, "skipped": 0,
                   "errors": []}

    # recent-update skip
    acct = _account(updated_at=datetime.now(UTC),
                    token_expires_at=datetime.now(UTC) + timedelta(days=5))
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert out["skipped"] == 1 and out["refreshed"] == 0

    # no oauth client → skipped
    acct = _account(platform="whatsapp")
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert out["skipped"] == 1


@pytest.mark.asyncio
async def test_refresh_async_paths(monkeypatch):
    monkeypatch.setattr(TR, "decrypt_token", lambda t: f"dec-{t}")
    monkeypatch.setattr(TR, "encrypt_token", lambda t: f"enc-{t}")

    fake_client = SimpleNamespace(
        refresh_token=AsyncMock(return_value={
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 3600}))
    monkeypatch.setattr(TR, "_get_oauth_client", lambda p: fake_client)

    # happy — enc + expiry set
    acct = _account()
    db = _db_with([acct])
    monkeypatch.setattr(TR, "_worker_db", lambda: db())
    out = await TR._refresh_expiring_tokens_async()
    assert out["refreshed"] == 1
    assert acct.access_token_enc == "enc-AT"
    assert acct.refresh_token_enc == "enc-RT"
    assert acct.status == "active"
    assert acct.token_expires_at > datetime.now(UTC) + timedelta(minutes=50)

    # twitter — granted scopes persisted
    acct = _account(platform="twitter")
    fake_client.refresh_token = AsyncMock(return_value={
        "access_token": "AT", "scope": "tweet.read tweet.write users.read"})
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    await TR._refresh_expiring_tokens_async()
    assert acct.scopes == ["tweet.read", "tweet.write", "users.read"]

    # tiktok — no expires_in → 24h default
    acct = _account(platform="tiktok")
    fake_client.refresh_token = AsyncMock(return_value={
        "access_token": "AT"})
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    await TR._refresh_expiring_tokens_async()
    assert acct.token_expires_at > datetime.now(UTC) + timedelta(hours=20)

    # linkedin — no expires_in → expiry cleared
    acct = _account(platform="linkedin")
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    await TR._refresh_expiring_tokens_async()
    assert acct.token_expires_at is None

    # refresh raises → expired + error recorded
    acct = _account()
    fake_client.refresh_token = AsyncMock(side_effect=RuntimeError("rev"))
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert out["errors"] and acct.status == "expired"

    # no access_token in response → expired
    acct = _account()
    fake_client.refresh_token = AsyncMock(return_value={"refresh_token": "x"})
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert "no access_token" in out["errors"][0]
    assert acct.status == "expired"


@pytest.mark.asyncio
async def test_refresh_meta_self_paths(monkeypatch):
    monkeypatch.setattr(TR, "decrypt_token", lambda t: f"dec-{t}")
    monkeypatch.setattr(TR, "encrypt_token", lambda t: f"enc-{t}")
    monkeypatch.setattr(TR, "_get_oauth_client", lambda p: SimpleNamespace())

    # IG business_login without refresh_token → ig_refresh_token grant
    acct = _account(platform="instagram", refresh_token_enc=None,
                    meta_data={"login_type": "business_login"})
    ig = AsyncMock(return_value={"access_token": "IG", "expires_in": 60})
    monkeypatch.setattr(TR, "_refresh_instagram_business_token", ig)
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert out["refreshed"] == 1
    ig.assert_awaited_once_with("dec-enc_a")

    # threads without refresh_token → th_refresh_token grant
    acct = _account(platform="threads", refresh_token_enc=None)
    th = AsyncMock(return_value={"access_token": "TH"})
    monkeypatch.setattr(TR, "_refresh_threads_token", th)
    monkeypatch.setattr(TR, "_worker_db", lambda: _db_with([acct])())
    out = await TR._refresh_expiring_tokens_async()
    assert out["refreshed"] == 1
    # threads w/o expires_in → 60-day meta default
    assert acct.token_expires_at > datetime.now(UTC) + timedelta(days=50)


def test_task_wrapper(monkeypatch):
    calls = []

    def fake_ra(coro):
        calls.append(coro)
        coro.close()
        return {"refreshed": 2}

    monkeypatch.setattr(TR, "run_async", fake_ra)
    out = TR.refresh_expiring_tokens()
    assert out == {"refreshed": 2}
    # run_async called twice — refresh + D1 sync
    assert len(calls) == 2

    # no refresh → no D1 sync
    calls.clear()
    monkeypatch.setattr(TR, "run_async",
                        lambda c: (c.close(), {"refreshed": 0})[1])
    TR.refresh_expiring_tokens()
    assert len(calls) == 0
