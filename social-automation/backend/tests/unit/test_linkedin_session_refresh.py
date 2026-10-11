"""Coverage for app/worker/tasks/linkedin_session_refresh.py."""
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_session_refresh as T


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _HTTP:
    """Route fake by URL suffix."""

    def __init__(self, routes, calls):
        self._routes = routes
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self._calls.append(url)
        for suffix, val in self._routes.items():
            if url.endswith(suffix):
                if isinstance(val, Exception):
                    raise val
                return _Resp(val)
        raise RuntimeError(f"unrouted {url}")

    async def post(self, url, **kw):
        self._calls.append(url)
        for suffix, val in self._routes.items():
            if url.endswith(suffix):
                if isinstance(val, Exception):
                    raise val
                return _Resp(val)
        raise RuntimeError(f"unrouted {url}")


def _acct(meta=None):
    return SimpleNamespace(id=uuid.uuid4(), platform="linkedin",
                           status="active", meta_data=meta or {})


def _db(accounts):
    class _DB:
        def __init__(self):
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(
                all=lambda: accounts))
    return _DB()


def _wire(monkeypatch, accounts, routes):
    calls = []
    monkeypatch.setattr(T, "_worker_db", lambda: _db(accounts))
    monkeypatch.setattr(T.httpx, "AsyncClient",
                        lambda **kw: _HTTP(routes, calls))
    monkeypatch.setattr("app.worker.tasks.linkedin_session_refresh."
                        "flag_modified", lambda *a: None)
    return calls


class TestRefresh:
    @pytest.mark.asyncio
    async def test_no_accounts(self, monkeypatch):
        _wire(monkeypatch, [], {})
        out = await T._refresh_linkedin_sessions_async()
        assert out["accounts_checked"] == 0

    @pytest.mark.asyncio
    async def test_valid_session(self, monkeypatch):
        acct = _acct()
        _wire(monkeypatch, [acct], {
            "/health": {"has_session": True, "rate_limited": False},
            "/session/status": {"logged_in": True},
        })
        out = await T._refresh_linkedin_sessions_async()
        assert out["sessions_valid"] == 1
        assert acct.meta_data["linkedin_session_status"] == "valid"

    @pytest.mark.asyncio
    async def test_expired_session(self, monkeypatch):
        acct = _acct()
        _wire(monkeypatch, [acct], {
            "/health": {"has_session": True, "rate_limited": False},
            "/session/status": {"logged_in": False},
        })
        out = await T._refresh_linkedin_sessions_async()
        assert out["sessions_invalid"] == 1
        assert acct.meta_data["linkedin_session_status"] == "expired"

    @pytest.mark.asyncio
    async def test_no_session(self, monkeypatch):
        acct = _acct()
        _wire(monkeypatch, [acct], {
            "/health": {"has_session": False, "rate_limited": False},
        })
        await T._refresh_linkedin_sessions_async()
        assert acct.meta_data["linkedin_session_status"] == "no_session"

    @pytest.mark.asyncio
    async def test_rate_limited_recent(self, monkeypatch):
        acct = _acct()
        calls = _wire(monkeypatch, [acct], {
            "/health": {"has_session": True, "rate_limited": True,
                        "rate_limit_until": (time.time() + 3600) * 1000},
        })
        out = await T._refresh_linkedin_sessions_async()
        assert acct.meta_data["linkedin_session_status"] == "rate_limited"
        assert not any("clear-rate-limit" in u for u in calls)
        assert out["rate_limits_cleared"] == 0

    @pytest.mark.asyncio
    async def test_rate_limited_stale_cleared(self, monkeypatch):
        acct = _acct()
        stale = (time.time() - 7 * 3600) * 1000
        calls = _wire(monkeypatch, [acct], {
            "/health": {"has_session": True, "rate_limited": True,
                        "rate_limit_until": stale},
            "/session/clear-rate-limit": {},
            "/session/status": {"logged_in": True},
        })
        out = await T._refresh_linkedin_sessions_async()
        assert out["rate_limits_cleared"] == 1
        # cleared → treated as valid session afterwards
        assert out["sessions_valid"] == 1
        assert any("clear-rate-limit" in u for u in calls)

    @pytest.mark.asyncio
    async def test_clear_rate_limit_failure_swallowed(self, monkeypatch):
        acct = _acct()
        stale = (time.time() - 7 * 3600) * 1000
        _wire(monkeypatch, [acct], {
            "/health": {"has_session": True, "rate_limited": True,
                        "rate_limit_until": stale},
            "/session/clear-rate-limit": RuntimeError("boom"),
        })
        out = await T._refresh_linkedin_sessions_async()
        # still rate_limited → status recorded, no status call needed
        assert acct.meta_data["linkedin_session_status"] == "rate_limited"
        assert out["rate_limits_cleared"] == 0

    @pytest.mark.asyncio
    async def test_health_error_counted(self, monkeypatch):
        acct = _acct()
        _wire(monkeypatch, [acct], {
            "/health": RuntimeError("sidecar down"),
        })
        out = await T._refresh_linkedin_sessions_async()
        assert out["errors"] == 1
        assert out["accounts_checked"] == 1


class TestRunAsync:
    def test_sync_path(self):
        async def _coro():
            return 5
        assert T._run_async(_coro()) == 5

    @pytest.mark.asyncio
    async def test_running_loop_path(self):
        async def _coro():
            return 6
        assert T._run_async(_coro()) == 6

    @pytest.mark.asyncio
    async def test_running_loop_error(self):
        async def _coro():
            raise RuntimeError("inner")
        with pytest.raises(RuntimeError):
            T._run_async(_coro())

    @pytest.mark.asyncio
    async def test_wrapper(self, monkeypatch):
        async def _stub():
            return {"done": 1}
        monkeypatch.setattr(T, "_refresh_linkedin_sessions_async",
                            _stub)
        assert T.refresh_linkedin_sessions() == {"done": 1}
