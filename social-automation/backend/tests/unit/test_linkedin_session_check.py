"""Coverage for app/worker/tasks/linkedin_session_check.py."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_session_check as T
from app.services.linkedin_sidecar import LinkedInSidecarError


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _results(*items):
    out = iter(items)

    def _next(*a, **kw):
        return next(out)
    return _next


def _scalars(items):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(
        all=lambda: items))


def _scalar(val):
    return SimpleNamespace(scalar_one_or_none=lambda: val)


def _count(n):
    return SimpleNamespace(scalar_one=lambda: n)


def _db(results):
    it = _results(*results)
    return SimpleNamespace(execute=AsyncMock(side_effect=it),
                           commit=AsyncMock())


def _wire(monkeypatch, *, health=None, refresh=None, db_results=()):
    monkeypatch.setattr(T, "get_settings", lambda: SimpleNamespace(
        LINKEDIN_BROWSER_SIDECAR_URL="http://sc"))
    client = SimpleNamespace(
        health=AsyncMock(
            side_effect=health if isinstance(health, Exception)
            else None,
            return_value=health if not isinstance(health, Exception)
            else None),
        refresh_session=AsyncMock(
            side_effect=refresh if isinstance(refresh, Exception)
            else None,
            return_value=refresh if not isinstance(refresh, Exception)
            else None),
    )
    monkeypatch.setattr(T, "LinkedInSidecarClient", lambda **kw: client)
    dbs = [_db(db_results)]
    monkeypatch.setattr(T, "_worker_db", lambda: _Session(dbs[0]))
    return client, dbs[0]


class TestRunCheck:
    @pytest.mark.asyncio
    async def test_health_raises(self, monkeypatch):
        _wire(monkeypatch, health=RuntimeError("down"))
        out = await T._run_check()
        assert out["sidecar_up"] is False

    @pytest.mark.asyncio
    async def test_health_not_ok(self, monkeypatch):
        _wire(monkeypatch, health={"status": "starting"})
        out = await T._run_check()
        assert out["sidecar_up"] is False

    @pytest.mark.asyncio
    async def test_alive_cookies_and_restore(self, monkeypatch):
        import app.services.secret_store as ss
        store_set = AsyncMock()
        monkeypatch.setattr(ss, "secret_store",
                            SimpleNamespace(set=store_set))
        expired = SimpleNamespace(id=uuid.uuid4(), status="expired",
                                  team_id=uuid.uuid4())
        _, db = _wire(
            monkeypatch,
            health={"status": "ok"},
            refresh={"logged_in": True,
                     "cookies": {"li_at": "cookie-val"}},
            db_results=[_scalars([expired])])
        out = await T._run_check()
        assert out["session_alive"] is True
        assert out["cookies_refreshed"] is True
        assert out["accounts_restored"] == 1
        assert expired.status == "active"
        store_set.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alive_no_cookies(self, monkeypatch):
        _wire(monkeypatch, health={"status": "ok"},
              refresh={"logged_in": True, "cookies": {}},
              db_results=[_scalars([])])
        out = await T._run_check()
        assert out["cookies_refreshed"] is False

    @pytest.mark.asyncio
    async def test_cookie_save_fails(self, monkeypatch):
        import app.services.secret_store as ss
        monkeypatch.setattr(ss, "secret_store", SimpleNamespace(
            set=AsyncMock(side_effect=RuntimeError("d1 down"))))
        _wire(monkeypatch, health={"status": "ok"},
              refresh={"logged_in": True,
                       "cookies": {"li_at": "v"}},
              db_results=[_scalars([])])
        out = await T._run_check()
        assert out["cookies_refreshed"] is True  # still counted

    @pytest.mark.asyncio
    async def test_429_inconclusive(self, monkeypatch):
        _wire(monkeypatch, health={"status": "ok"},
              refresh=LinkedInSidecarError(429, "rate limited"))
        out = await T._run_check()
        assert out["inconclusive"] is True
        assert out["session_alive"] is False

    @pytest.mark.asyncio
    async def test_401_marks_expired_with_owner(self, monkeypatch):
        active_acct = SimpleNamespace(id=uuid.uuid4(), status="active",
                                      team_id=uuid.uuid4())
        owner = SimpleNamespace(email="o@x.co", name="O")
        alert = AsyncMock()
        monkeypatch.setattr(T, "_send_alert", alert)
        _, db = _wire(
            monkeypatch, health={"status": "ok"},
            refresh=LinkedInSidecarError(401, "expired"),
            db_results=[_scalars([active_acct]), _scalar(owner)])
        out = await T._run_check()
        assert out["accounts_marked"] == 1
        assert active_acct.status == "expired"
        alert.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_401_no_owner_and_already_expired(self, monkeypatch):
        already = SimpleNamespace(id=uuid.uuid4(), status="expired",
                                  team_id=uuid.uuid4())
        _, db = _wire(
            monkeypatch, health={"status": "ok"},
            refresh=LinkedInSidecarError(401, "x"),
            db_results=[_scalars([already]), _scalar(None)])
        out = await T._run_check()
        assert out["accounts_marked"] == 0

    @pytest.mark.asyncio
    async def test_5xx_inconclusive(self, monkeypatch):
        _wire(monkeypatch, health={"status": "ok"},
              refresh=LinkedInSidecarError(500, "boom"))
        out = await T._run_check()
        assert out["inconclusive"] is True


def _alert_db(recent_count):
    return SimpleNamespace(
        execute=AsyncMock(return_value=_count(recent_count)))


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_cooldown_skips(self, monkeypatch):
        monkeypatch.setattr(T, "task_session",
                            lambda: _Session(_alert_db(1)))
        slack = AsyncMock()
        monkeypatch.setattr(T, "post_alert_to_slack", slack)
        await T._send_alert(SimpleNamespace(email="o@x.co", name="O"),
                            "reason")
        slack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_slack_and_email(self, monkeypatch):
        monkeypatch.setattr(T, "task_session",
                            lambda: _Session(_alert_db(0)))
        slack = AsyncMock()
        monkeypatch.setattr(T, "post_alert_to_slack", slack)
        monkeypatch.setattr(T, "session_heal_buttons",
                            lambda: [{"b": 1}])
        import app.services.email_templates as et
        mail = AsyncMock()
        monkeypatch.setattr(et, "send_linkedin_session_alert_email",
                            mail)
        await T._send_alert(
            SimpleNamespace(email="o@x.co", name="O"), "timeout")
        slack.assert_awaited_once()
        mail.assert_awaited_once()
        assert mail.await_args.kwargs["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(T, "task_session", lambda: _Session(
            SimpleNamespace(execute=AsyncMock(
                side_effect=RuntimeError("db")))))
        await T._send_alert(SimpleNamespace(email="o", name=""), "r")


def test_wrapper(monkeypatch):
    monkeypatch.setattr(T, "run_async", lambda c: c.close() or {"ok": 1})
    assert T.check_linkedin_sessions() == {"ok": 1}
