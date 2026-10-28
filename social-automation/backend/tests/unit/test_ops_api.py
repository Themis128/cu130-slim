"""Coverage for app/api/ops.py — ops console, digests, probes."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.api.ops as OPS


def _user():
    return SimpleNamespace(id=uuid.uuid4())


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, scalars=None, rows=None, scalar=None):
        self._scalars = scalars or []
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._scalars)

    def all(self):
        return self._rows if self._rows is not None else self._scalars

    def scalar(self):
        return self._scalar


class _DB:
    def __init__(self, results):
        self._q = list(results)
        self.commits = 0

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Result()

    async def commit(self):
        self.commits += 1


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class _HTTP:
    def __init__(self, responses=None, exc=None):
        self._responses = responses or {}
        self._exc = exc
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        self.requests.append(url)
        if self._exc:
            raise self._exc
        r = self._responses.get(url)
        if isinstance(r, BaseException):
            raise r
        return r or _Resp()


def _wire_http(monkeypatch, client):
    monkeypatch.setattr(OPS.httpx, "AsyncClient", lambda **kw: client)


class TestOrchestratorEndpoints:
    @pytest.mark.asyncio
    async def test_status_held(self, monkeypatch):
        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "get_current_platform", AsyncMock(return_value="linkedin"))
        monkeypatch.setattr(bo, "get_queue_length", AsyncMock(return_value=3))
        r = await OPS.get_browser_orchestrator_status(current_user=_user())
        assert r.current_platform == "linkedin"
        assert r.queue_length == 3
        assert r.lock_held
        assert "linkedin" in r.message

    @pytest.mark.asyncio
    async def test_status_idle(self, monkeypatch):
        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "get_current_platform", AsyncMock(return_value=None))
        monkeypatch.setattr(bo, "get_queue_length", AsyncMock(return_value=0))
        r = await OPS.get_browser_orchestrator_status(current_user=_user())
        assert not r.lock_held
        assert r.message == "Browser idle"

    @pytest.mark.asyncio
    async def test_status_error(self, monkeypatch):
        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "get_current_platform",
                            AsyncMock(side_effect=RuntimeError("redis down")))
        r = await OPS.get_browser_orchestrator_status(current_user=_user())
        assert "unavailable" in r.message

    @pytest.mark.asyncio
    async def test_release_ok_and_fail(self, monkeypatch):
        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "force_release_lock", AsyncMock(return_value=True))
        r = await OPS.force_release_browser_lock(current_user=_user())
        assert r.message == "Lock force-released"
        bo.force_release_lock = AsyncMock(return_value=False)
        r = await OPS.force_release_browser_lock(current_user=_user())
        assert r.message == "Force-release failed"
        bo.force_release_lock = AsyncMock(side_effect=RuntimeError("x"))
        r = await OPS.force_release_browser_lock(current_user=_user())
        assert "Force-release failed" in r.message


class TestSessionHeal:
    @pytest.mark.asyncio
    async def test_delegates(self, monkeypatch):
        import app.services.session_healer as sh
        monkeypatch.setattr(sh, "heal_all_sessions",
                            AsyncMock(return_value={"linkedin": "ok"}))
        out = await OPS.trigger_session_heal(current_user=_user())
        assert out == {"linkedin": "ok"}


class TestDailyDigest:
    @pytest.mark.asyncio
    async def test_queued(self, monkeypatch):
        delay = Mock()
        task = SimpleNamespace(delay=delay)
        monkeypatch.setattr(OPS, "send_daily_slack_digest", task)
        r = await OPS.trigger_daily_digest(
            days=1, post_to_slack=True, post_to_email=False,
            async_queue=True, current_user=_user(), db=None)
        assert r.queued
        delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_inline_report(self, monkeypatch):
        reports = [
            {"posted_to_slack": True, "emailed": True},
            {"slack_error": "webhook dead"},
        ]
        monkeypatch.setattr(OPS, "run_daily_digest_for_all_teams",
                            AsyncMock(return_value=reports))
        r = await OPS.trigger_daily_digest(
            days=3, post_to_slack=True, post_to_email=True,
            async_queue=False, current_user=_user(), db=None)
        assert r.reports == reports
        assert "Slack 1" in r.message
        assert "email 1" in r.message
        assert "webhook dead" in r.message

    @pytest.mark.asyncio
    async def test_preview(self, monkeypatch):
        run = AsyncMock(return_value=[{"team": "t1"}])
        monkeypatch.setattr(OPS, "run_daily_digest_for_all_teams", run)
        r = await OPS.preview_daily_digest(current_user=_user(), db=None)
        assert "Preview" in r.message
        run.assert_awaited_once()


class TestPaddleDigest:
    @pytest.mark.asyncio
    async def test_trigger(self, monkeypatch):
        send = AsyncMock(return_value={"text": "digest", "posted": True})
        monkeypatch.setattr(OPS, "send_paddle_digest_to_slack", send)
        r = await OPS.trigger_paddle_digest(current_user=_user())
        assert r.text == "digest" and r.posted

    @pytest.mark.asyncio
    async def test_trigger_with_error(self, monkeypatch):
        send = AsyncMock(return_value={"text": "", "posted": False,
                                       "error": "no webhook"})
        monkeypatch.setattr(OPS, "send_paddle_digest_to_slack", send)
        r = await OPS.trigger_paddle_digest(current_user=_user())
        assert r.error == "no webhook"

    @pytest.mark.asyncio
    async def test_preview(self, monkeypatch):
        send = AsyncMock(return_value={"text": "t", "posted": True})
        monkeypatch.setattr(OPS, "send_paddle_digest_to_slack", send)
        r = await OPS.preview_paddle_digest(current_user=_user())
        assert r.text == "t" and not r.posted and r.error is None
        send.assert_awaited_once_with(post_to_slack=False)

    @pytest.mark.asyncio
    async def test_billing_aliases(self, monkeypatch):
        send = AsyncMock(return_value={"text": "d", "posted": True})
        monkeypatch.setattr(OPS, "send_paddle_digest_to_slack", send)
        r = await OPS.trigger_billing_digest(current_user=_user())
        assert r.posted
        r = await OPS.preview_billing_digest(current_user=_user())
        assert r.text == "d"


class TestProbeService:
    @pytest.mark.asyncio
    async def test_sleeping_container(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="stopped"))
        r = await OPS._probe_service("svc", "http://x", container="svc-c")
        assert r.online and r.detail == "sleeping"

    @pytest.mark.asyncio
    async def test_http_ok(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        _wire_http(monkeypatch, _HTTP({"http://h": _Resp(200)}))
        r = await OPS._probe_service("svc", "http://h")
        assert r.online and r.detail == "200"

    @pytest.mark.asyncio
    async def test_http_5xx_offline(self, monkeypatch):
        _wire_http(monkeypatch, _HTTP({"http://h": _Resp(503)}))
        r = await OPS._probe_service("svc", "http://h")
        assert not r.online and r.detail == "503"

    @pytest.mark.asyncio
    async def test_exception_running_is_busy(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        _wire_http(monkeypatch, _HTTP(exc=TimeoutError()))
        r = await OPS._probe_service("svc", "http://h", container="c")
        assert r.online and "busy" in r.detail

    @pytest.mark.asyncio
    async def test_exception_no_container_offline(self, monkeypatch):
        _wire_http(monkeypatch, _HTTP(exc=TimeoutError()))
        r = await OPS._probe_service("svc", "http://h")
        assert not r.online and r.detail == "TimeoutError"


class TestProbeComfyui:
    @pytest.mark.asyncio
    async def test_sleeping(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="stopped"))
        status, q = await OPS._probe_comfyui("http://c")
        assert status.online and status.detail == "sleeping"
        assert q == {}

    @pytest.mark.asyncio
    async def test_running_with_queue(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        c = _HTTP({
            "http://c/system_stats": _Resp(200),
            "http://c/queue": _Resp(
                200, {"queue_pending": [1, 2], "queue_running": [1]}),
        })
        _wire_http(monkeypatch, c)
        status, q = await OPS._probe_comfyui("http://c")
        assert status.online
        assert q == {"pending": 2, "running": 1}

    @pytest.mark.asyncio
    async def test_stats_not_ok(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        c = _HTTP({
            "http://c/system_stats": _Resp(500),
            "http://c/queue": _Resp(404, {}),
        })
        _wire_http(monkeypatch, c)
        status, q = await OPS._probe_comfyui("http://c")
        assert not status.online and q == {}

    @pytest.mark.asyncio
    async def test_exception_running_busy(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        _wire_http(monkeypatch, _HTTP(exc=TimeoutError()))
        status, q = await OPS._probe_comfyui("http://c")
        assert status.online and "busy" in status.detail

    @pytest.mark.asyncio
    async def test_exception_stopped_offline(self, monkeypatch):
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="unknown"))
        _wire_http(monkeypatch, _HTTP(exc=OSError()))
        status, q = await OPS._probe_comfyui("http://c")
        assert not status.online and q == {}


def _acc(platform="instagram", audit=None):
    return SimpleNamespace(
        id=uuid.uuid4(), platform=platform, username="u",
        display_name="U", status="connected", account_type="person",
        token_expires_at=None,
        meta_data={"audit": audit} if audit else {})


class TestOpsConsole:
    @pytest.mark.asyncio
    async def test_console_happy(self, monkeypatch):
        monkeypatch.setattr(OPS, "get_settings", lambda: SimpleNamespace(
            BROWSER_BRIDGE_URL="http://bb",
            LINKEDIN_BROWSER_SIDECAR_URL="http://li",
            FACEBOOK_BROWSER_SIDECAR_URL="http://fb",
            TIKTOK_BROWSER_SIDECAR_URL="http://tt",
            COMFYUI_URL="http://cf/"))
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        http = _HTTP({u: _Resp(200) for u in [
            "http://bb/health", "http://li/health",
            "http://fb/health", "http://tt/health",
            "http://cf/system_stats",
        ]})
        http._responses["http://cf/queue"] = _Resp(
            200, {"queue_pending": [], "queue_running": [1]})
        _wire_http(monkeypatch, http)

        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "get_current_platform",
                            AsyncMock(return_value="tiktok"))
        monkeypatch.setattr(bo, "get_queue_length", AsyncMock(return_value=2))

        acc = _acc("tiktok", audit={"status": "approved"})
        db = _DB([
            _Result(scalars=[acc]),
            _Result(rows=[("queued", 5), ("failed", 1)]),
            _Result(scalar=7),
        ])
        team = uuid.uuid4()
        r = await OPS.ops_console(team_id=team, current_user=_user(), db=db)
        assert len(r.services) == 5
        assert all(s.online for s in r.services)
        assert r.accounts[0].platform == "tiktok"
        assert r.publish_queue == {"queued": 5, "failed": 1}
        assert r.media["ai_generated_assets"] == 7
        assert r.media["comfyui_queue"]["running"] == 1
        assert r.browser_orchestrator.current_platform == "tiktok"
        assert r.tiktok_audit == {"status": "approved"}

    @pytest.mark.asyncio
    async def test_console_orchestrator_error(self, monkeypatch):
        monkeypatch.setattr(OPS, "get_settings", lambda: SimpleNamespace(
            BROWSER_BRIDGE_URL="http://bb",
            LINKEDIN_BROWSER_SIDECAR_URL="http://li",
            FACEBOOK_BROWSER_SIDECAR_URL="http://fb",
            TIKTOK_BROWSER_SIDECAR_URL="http://tt",
            COMFYUI_URL="http://cf"))
        monkeypatch.setattr(OPS.stack_ops, "service_state",
                            AsyncMock(return_value="running"))
        _wire_http(monkeypatch, _HTTP(exc=TimeoutError()))

        import app.services.browser_orchestrator as bo
        monkeypatch.setattr(bo, "get_current_platform",
                            AsyncMock(side_effect=RuntimeError("redis")))
        db = _DB([
            _Result(scalars=[]),
            _Result(rows=[]),
            _Result(scalar=None),
        ])
        r = await OPS.ops_console(team_id=uuid.uuid4(),
                                  current_user=_user(), db=db)
        assert "unavailable" in r.browser_orchestrator.message
        assert r.media["ai_generated_assets"] == 0
        assert r.tiktok_audit is None
        # probes timed out but containers "running" → busy
        assert all(s.online for s in r.services)


class TestTikTokAudit:
    @pytest.mark.asyncio
    async def test_no_accounts(self):
        db = _DB([_Result(scalars=[])])
        out = await OPS.update_tiktok_audit(
            OPS.TikTokAuditUpdate(status="pending_review"),
            team_id=uuid.uuid4(), current_user=_user(), db=db)
        assert out["updated"] == 0
        assert "no TikTok" in out["message"]

    @pytest.mark.asyncio
    async def test_updates_accounts(self):
        acc = _acc("tiktok")
        db = _DB([_Result(scalars=[acc])])
        body = OPS.TikTokAuditUpdate(
            status="approved", reference="ref-1", detail="ok")
        out = await OPS.update_tiktok_audit(
            body, team_id=uuid.uuid4(), current_user=_user(), db=db)
        assert out["updated"] == 1
        assert out["audit"]["status"] == "approved"
        assert out["audit"]["reference"] == "ref-1"
        assert acc.meta_data["audit"]["status"] == "approved"
        assert db.commits == 1
