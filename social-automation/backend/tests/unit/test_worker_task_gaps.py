"""Coverage-gap tests for thin Celery task wrappers and core helpers —
``run_async``/``task_session`` wiring, the rate-limit key function, and
path sanitization edge cases.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# ── app.worker.tasks.monetization ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_monetization_task_run_inner(monkeypatch):
    import app.worker.tasks.monetization as T

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(T, "_worker_db", lambda: _Ctx())
    runner = AsyncMock(return_value=[{"team_name": "Cloudless"}])
    monkeypatch.setattr(
        "app.services.monetization_report.run_monetization_report_for_all_teams",
        runner,
    )
    out = await T._run(send=False, execute=True, insight_days=14)
    assert out == {"reports": [{"team_name": "Cloudless"}]}
    runner.assert_awaited_once()
    assert runner.call_args.kwargs == {"insight_days": 14, "send": False, "execute": True}


def test_monetization_task_delegates_to_run_async(monkeypatch):
    import app.worker.tasks.monetization as T

    captured = {}

    def fake_run_async(coro):
        captured["coro"] = coro
        coro.close()  # avoid "never awaited" warning
        return {"ok": True}

    monkeypatch.setattr(T, "run_async", fake_run_async)
    out = T.run_daily_monetization_report(send=False, execute=False, insight_days=7)
    assert out == {"ok": True}
    assert "coro" in captured


# ── app.worker.tasks.session_healer ───────────────────────────────────────


def test_heal_sessions_delegates(monkeypatch):
    import app.worker.tasks.session_healer as T

    async def fake_heal():
        return {"healed": ["linkedin"]}

    def fake_run_async(coro):
        coro.close()
        return {"healed": ["linkedin"]}

    monkeypatch.setattr(T, "run_async", fake_run_async)
    monkeypatch.setattr("app.services.session_healer.heal_all_sessions", fake_heal)
    out = T.heal_sessions()
    assert out == {"healed": ["linkedin"]}


# ── app.worker.tasks.telegram_agent ───────────────────────────────────────


def test_telegram_agent_task_delegates(monkeypatch):
    import app.worker.tasks.telegram_agent as T

    async def fake_sweep():
        return {"accounts": 1}

    def fake_run_async(coro):
        coro.close()
        return {"accounts": 1}

    monkeypatch.setattr(T, "run_async", fake_run_async)
    monkeypatch.setattr(T, "run_agent_sweep", fake_sweep)
    out = T.run_channel_agent_daily()
    assert out == {"accounts": 1}


# ── app.worker.tasks.notebook_reports fallbacks ───────────────────────────


@pytest.mark.asyncio
async def test_fallback_strategy_delegates(monkeypatch):
    import app.worker.tasks.notebook_reports as T

    monkeypatch.setattr(
        "app.worker.tasks.digest._send_strategy_async",
        AsyncMock(return_value={"emailed": True}),
    )
    out = await T._fallback_strategy({"insight_days": 7}, send=False)
    assert out == {"emailed": True}


@pytest.mark.asyncio
async def test_fallback_monetization_delegates(monkeypatch):
    import app.worker.tasks.notebook_reports as T

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.db.session.async_session_maker", lambda: _Ctx())
    runner = AsyncMock(return_value=[{"team_name": "T"}])
    monkeypatch.setattr(
        "app.services.monetization_report.run_monetization_report_for_all_teams",
        runner,
    )
    out = await T._fallback_monetization({"insight_days": 14, "execute": False}, send=True)
    assert out == [{"team_name": "T"}]
    assert runner.call_args.kwargs["insight_days"] == 14
    assert runner.call_args.kwargs["execute"] is False


# ── app.core.limiter ──────────────────────────────────────────────────────


def test_rate_limit_key_production_uses_remote_addr(monkeypatch):
    import app.core.limiter as L

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(L, "settings", SimpleNamespace(APP_ENV="production"))
    request = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.7"),
        headers={},
        scope={"type": "http"},
        app=SimpleNamespace(state=SimpleNamespace()),
        url=SimpleNamespace(path="/"),
        method="GET",
        base_url="http://t/",
        cookies={},
        query_params={},
        path_params={},
        state=SimpleNamespace(),
    )
    # starlette Request protocol — get_remote_address reads request.client.host
    key = L._rate_limit_key(request)
    assert key == "203.0.113.7"


def test_rate_limit_key_test_env_is_unique():
    import app.core.limiter as L

    k1 = L._rate_limit_key(None)
    k2 = L._rate_limit_key(None)
    assert k1 != k2 and uuid.UUID(k1)


# ── app.core.path_utils ───────────────────────────────────────────────────


def test_sanitize_edges():
    from app.core.path_utils import safe_path_component

    assert safe_path_component("") == "unnamed"
    assert safe_path_component("..") == "unnamed"
    assert safe_path_component("a/b\\c\x00d") == "a_b_c_d"
    assert safe_path_component("normal-name.pdf") == "normal-name.pdf"
