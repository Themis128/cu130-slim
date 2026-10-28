"""Unit tests for the latency watchdog (edge SLO breach -> wrangler rollback)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.worker.tasks import latency_watchdog as W


class _FakeClient:
    def __init__(self, *a, **kw):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        self.calls.append(url)
        return SimpleNamespace(status_code=200)


@pytest.fixture(autouse=True)
def _reset_counter(monkeypatch):
    monkeypatch.setattr(W, "_breach_count", 0)


@pytest.mark.asyncio
async def test_measure_returns_ms(monkeypatch):
    monkeypatch.setattr(W.httpx, "AsyncClient", _FakeClient)
    ms = await W._measure()
    assert ms >= 0


@pytest.mark.asyncio
async def test_rollback_without_wrangler(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert await W._rollback() is False


class _Proc:
    def __init__(self, rc):
        self.returncode = rc

    async def communicate(self):
        return b"", b"boom"


@pytest.mark.asyncio
@pytest.mark.parametrize("rc,expected", [(0, True), (1, False)])
async def test_rollback_with_wrangler(monkeypatch, rc, expected):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wrangler")
    seen = {}

    async def fake_exec(*args, **kw):
        seen["args"] = args
        return _Proc(rc)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await W._rollback() is expected
    assert seen["args"][:2] == ("wrangler", "rollback")


def _measure_returning(ms):
    async def _m():
        return ms

    return _m


@pytest.mark.asyncio
async def test_check_under_slo_resets_counter(monkeypatch):
    monkeypatch.setattr(W, "_measure", _measure_returning(5.0))
    monkeypatch.setattr(W, "_breach_count", 2)
    out = await W._check()
    assert out == {"latency_ms": 5.0, "breach": 0, "rolled_back": False}
    assert W._breach_count == 0


@pytest.mark.asyncio
async def test_check_under_slo_no_prior_breach(monkeypatch):
    monkeypatch.setattr(W, "_measure", _measure_returning(1.0))
    out = await W._check()
    assert out["breach"] == 0


@pytest.mark.asyncio
async def test_check_breach_below_threshold_no_rollback(monkeypatch):
    monkeypatch.setattr(W, "_measure", _measure_returning(W._SLO_MS + 50))

    async def no_rollback():  # pragma: no cover - must not be called
        raise AssertionError("rollback should not run")

    monkeypatch.setattr(W, "_rollback", no_rollback)
    out = await W._check()
    assert out["breach"] == 1 and out["rolled_back"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("rolled,breach_after", [(True, 0), (False, None)])
async def test_check_threshold_triggers_rollback(monkeypatch, rolled, breach_after):
    monkeypatch.setattr(W, "_measure", _measure_returning(W._SLO_MS + 50))
    monkeypatch.setattr(W, "_breach_count", W._BREACH_THRESHOLD - 1)

    async def fake_rollback():
        return rolled

    monkeypatch.setattr(W, "_rollback", fake_rollback)
    out = await W._check()
    assert out["rolled_back"] is rolled
    expected = W._BREACH_THRESHOLD if breach_after is None else breach_after
    assert out["breach"] == expected


def test_run_latency_watchdog_idle_loop(monkeypatch):
    async def fake_check():
        return {"ok": 1}

    monkeypatch.setattr(W, "_check", fake_check)
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
        assert W.run_latency_watchdog() == {"ok": 1}
    finally:
        loop.close()


def test_run_latency_watchdog_running_loop(monkeypatch):
    async def fake_check():
        return {"ok": 2}

    monkeypatch.setattr(W, "_check", fake_check)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: SimpleNamespace(is_running=lambda: True))
    monkeypatch.setattr(asyncio, "run", lambda coro: (coro.close(), {"ok": 2})[1])
    assert W.run_latency_watchdog() == {"ok": 2}
