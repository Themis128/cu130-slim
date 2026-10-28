"""Unit tests for worker health/monitor tasks: dodo_live_check, dmr_health.

Redis and service calls are faked; the gating/skip/alert logic is real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.dmr_health as DH
import app.worker.tasks.dodo_live_check as DL


class _Redis:
    def __init__(self, store=None):
        self.store = store or {}
        self.closed = False

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v

    async def setex(self, k, ttl, v):
        self.store[k] = v

    async def aclose(self):
        self.closed = True


# ── dodo_live_check ─────────────────────────────────────────────────


def _dl_settings(**kw):
    base = {"billing_provider": "dodo", "DODO_ENVIRONMENT": "live_mode",
            "REDIS_URL": "redis://x"}
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_dl(monkeypatch, settings, redis, live, slack=None):
    # _check imports inside the function — patch the real modules
    import app.core.config as cfg
    import app.services.dodo_api as dodo
    import app.services.slack_notifications as slack_mod

    monkeypatch.setattr(cfg, "get_settings", lambda: settings)
    monkeypatch.setattr(DL, "_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(dodo, "live_payments_enabled", AsyncMock(return_value=live))
    monkeypatch.setattr(slack_mod, "post_alert_to_slack",
                        slack or AsyncMock())


@pytest.mark.asyncio
async def test_dodo_skipped_wrong_provider(monkeypatch):
    _patch_dl(monkeypatch, _dl_settings(billing_provider="polar"), _Redis(), True)
    out = await DL._check()
    assert out["skipped"] and "not active" in out["reason"]


@pytest.mark.asyncio
async def test_dodo_skipped_not_live_env(monkeypatch):
    _patch_dl(monkeypatch, _dl_settings(DODO_ENVIRONMENT="sandbox"), _Redis(), True)
    out = await DL._check()
    assert out["skipped"]


@pytest.mark.asyncio
async def test_dodo_skipped_already_confirmed(monkeypatch):
    r = _Redis({DL._CONFIRMED_KEY: "1"})
    _patch_dl(monkeypatch, _dl_settings(), r, True)
    out = await DL._check()
    assert out["skipped"] and "already confirmed" in out["reason"]


@pytest.mark.asyncio
async def test_dodo_not_live_yet(monkeypatch):
    r = _Redis()
    slack = AsyncMock()
    _patch_dl(monkeypatch, _dl_settings(), r, False, slack)
    out = await DL._check()
    assert out == {"live": False}
    assert DL._CONFIRMED_KEY not in r.store
    slack.assert_not_awaited()


@pytest.mark.asyncio
async def test_dodo_goes_live(monkeypatch):
    r = _Redis()
    slack = AsyncMock()
    _patch_dl(monkeypatch, _dl_settings(), r, True, slack)
    out = await DL._check()
    assert out == {"live": True}
    assert DL._CONFIRMED_KEY in r.store   # flag set → becomes no-op
    slack.assert_awaited_once()           # Slack alert fired
    assert r.closed                       # redis closed


@pytest.mark.asyncio
async def test_dodo_slack_failure_still_reports_live(monkeypatch):
    r = _Redis()
    slack = AsyncMock(side_effect=RuntimeError("slack down"))
    _patch_dl(monkeypatch, _dl_settings(), r, True, slack)
    out = await DL._check()
    assert out["live"] is True


def test_dodo_task_wrapper(monkeypatch):
    monkeypatch.setattr(DL, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert DL.check_dodo_live() == {"ok": 1}


@pytest.mark.asyncio
async def test_dodo_run_async_inside_running_loop(monkeypatch):
    """When a loop is already running, _run_async hops to a worker thread."""

    async def _coro():
        return 7

    def fake_run(coro):
        coro.close()
        return 7

    monkeypatch.setattr(DL, "run_async", fake_run)
    assert DL._run_async(_coro()) == 7


@pytest.mark.asyncio
async def test_dodo_redis_factory(monkeypatch):
    import redis.asyncio as aioredis

    import app.core.config as cfg

    sentinel = object()
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://x"))
    monkeypatch.setattr(aioredis, "from_url", lambda url: sentinel)
    assert await DL._redis() is sentinel


# ── dmr_health ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dmr_online_all_models(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "validate_dmr_models",
                        AsyncMock(return_value={"missing": []}))
    r = _Redis()

    import app.core.config as cfg
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(REDIS_URL="r"))

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda u: r)

    out = await DH._check()
    assert out["online"] is True and out["missing_models"] == []
    assert DH._STATUS_KEY in r.store and r.closed


@pytest.mark.asyncio
async def test_dmr_offline(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=False))
    monkeypatch.setattr(dmr, "validate_dmr_models", AsyncMock(
        side_effect=AssertionError("must not be called")))
    r = _Redis()
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(REDIS_URL="r"))
    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda u: r)

    out = await DH._check()
    assert out["online"] is False
    assert out["missing_models"] == []  # validation skipped when offline


@pytest.mark.asyncio
async def test_dmr_validation_error_and_redis_down(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "validate_dmr_models",
                        AsyncMock(side_effect=RuntimeError("broken")))
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "get_settings",
                        lambda: SimpleNamespace(REDIS_URL="redis://bad"))
    # real from_url will fail on setex? — use from_url that raises
    import redis.asyncio as aioredis

    class _Boom:
        def __init__(self, *a):
            pass
        async def setex(self, *a):
            raise ConnectionError("no redis")
        async def aclose(self):
            raise ConnectionError("no redis")

    monkeypatch.setattr(aioredis, "from_url", lambda u: _Boom())

    out = await DH._check()
    assert out["online"] is True and out["missing_models"] == []
    assert "latency_ms" in out


@pytest.mark.asyncio
async def test_dmr_missing_models(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "validate_dmr_models",
                        AsyncMock(return_value={"missing": ["llama3.2"]}))
    r = _Redis()
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(REDIS_URL="r"))
    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda u: r)

    out = await DH._check()
    assert out["missing_models"] == ["llama3.2"]


@pytest.mark.asyncio
async def test_keep_warm(monkeypatch):
    import app.services.dmr as dmr
    monkeypatch.setattr(dmr, "keep_warm_models",
                        AsyncMock(return_value={"warmed": 2}))
    assert await DH._keep_warm() == {"warmed": 2}


def test_dmr_task_wrappers(monkeypatch):
    monkeypatch.setattr(DH, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert DH.check_dmr_health() == {"ok": 1}
    assert DH.keep_warm_dmr_models() == {"ok": 1}


@pytest.mark.asyncio
async def test_dmr_run_async_inside_running_loop(monkeypatch):
    async def _coro():
        return {"warm": True}

    monkeypatch.setattr(DH, "run_async", lambda coro: (coro.close(), {"warm": True})[1])
    assert DH._run_async(_coro()) == {"warm": True}
