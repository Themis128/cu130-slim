"""Human-gate watcher — sidecar polling, 6h nudge dedup, cleared notices."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.worker.tasks import human_gate as HG

_RealClient = httpx.AsyncClient


class _Redis:
    def __init__(self, store=None):
        self.store = dict(store or {})

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)


def _setup(monkeypatch, handler, store=None):
    import redis.asyncio as aioredis

    import app.services.slack_notifications as SN

    r = _Redis(store)
    monkeypatch.setattr(aioredis, "from_url", lambda url: r)
    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)

    def _factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return _RealClient(*a, **kw)

    monkeypatch.setattr(HG.httpx, "AsyncClient", _factory)
    return r, alert


@pytest.mark.asyncio
async def test_watch_blocked_gates_nudge_once(monkeypatch):
    def handler(req):
        if "linkedin" in req.url.host:
            return httpx.Response(200, json={"status": "pending_2fa"})
        return httpx.Response(200, json={"checkpoint": "RESTRICTED"})

    r, alert = _setup(monkeypatch, handler)
    out = await HG._watch()
    assert out == {"linkedin-2fa": "nudged", "meta-status": "nudged"}
    assert alert.await_count == 2
    assert "humangate:nudge:linkedin-2fa" in r.store

    # second pass inside the 6h window → no new nudge
    out = await HG._watch()
    assert out == {"linkedin-2fa": "blocked (recently nudged)", "meta-status": "blocked (recently nudged)"}
    assert alert.await_count == 2


@pytest.mark.asyncio
async def test_watch_logged_out_false_is_blocked(monkeypatch):
    stale = str(time.time() - HG._NUDGE_SECONDS - 10)
    r, alert = _setup(
        monkeypatch,
        lambda req: httpx.Response(200, json={"logged_in": False}),
        {"humangate:nudge:linkedin-2fa": stale},
    )
    out = await HG._watch()
    assert out["linkedin-2fa"] == "nudged"
    assert float(r.store["humangate:nudge:linkedin-2fa"]) > float(stale)


@pytest.mark.asyncio
async def test_watch_cleared_and_ok_and_unreachable(monkeypatch):
    def handler(req):
        if "linkedin" in req.url.host:
            return httpx.Response(200, json={"status": "ok"})
        raise httpx.ConnectError("asleep", request=req)

    r, alert = _setup(monkeypatch, handler, {"humangate:nudge:linkedin-2fa": "1"})
    out = await HG._watch()
    assert out == {"linkedin-2fa": "cleared", "meta-status": "unreachable (ConnectError)"}
    assert "humangate:nudge:linkedin-2fa" not in r.store
    assert "cleared" in alert.await_args.args[0]

    out = await HG._watch()
    assert out["linkedin-2fa"] == "ok"


def test_run_human_gate_watch_disabled(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=False))
    assert HG.run_human_gate_watch() is None


def test_run_human_gate_watch_enabled_and_error(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=True))

    def _ok(coro):
        coro.close()
        return {"linkedin-2fa": "ok"}

    monkeypatch.setattr(HG, "run_async", _ok)
    assert HG.run_human_gate_watch() == {"linkedin-2fa": "ok"}

    def _boom(coro):
        coro.close()
        raise RuntimeError("loop gone")

    monkeypatch.setattr(HG, "run_async", _boom)
    assert HG.run_human_gate_watch() is None
