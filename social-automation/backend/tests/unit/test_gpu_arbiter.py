"""gpu_arbiter — Redis lock semantics with an in-memory Redis fake."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import gpu_arbiter as GA


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.closed = False

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def aclose(self):
        self.closed = True


def _patch_redis(monkeypatch, redis_obj=None, *, raise_exc=False):
    async def _fake():
        if raise_exc:
            raise ConnectionError("redis down")
        return redis_obj

    monkeypatch.setattr(GA, "_redis", _fake)


def _stub_dmr_and_httpx(monkeypatch):
    """Stub the finally-block dependencies (DMR unload + ComfyUI free)."""
    import sys
    import types

    dmr_mod = types.ModuleType("app.services.dmr")
    dmr_mod._unload_idle_models = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.dmr", dmr_mod)

    httpx_mod = types.ModuleType("httpx")

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, timeout=None):
            return MagicMock(status_code=200, json=lambda: {"queue_running": []})

        async def post(self, url, json=None, timeout=None):
            return MagicMock(status_code=200)

    httpx_mod.AsyncClient = _Client
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)


@pytest.mark.asyncio
async def test_media_gpu_busy_true_false_and_redis_down(monkeypatch):
    r = _FakeRedis()
    _patch_redis(monkeypatch, r)
    assert await GA.media_gpu_busy() is False
    r.store[GA._BUSY_KEY] = "1"
    assert await GA.media_gpu_busy() is True
    assert r.closed  # client closed after probe

    _patch_redis(monkeypatch, raise_exc=True)
    assert await GA.media_gpu_busy() is False  # fail-open


@pytest.mark.asyncio
async def test_await_media_idle_returns_immediately(monkeypatch):
    _patch_redis(monkeypatch, _FakeRedis())
    await GA.await_media_idle(max_wait_s=0.01)  # no busy flag → instant


@pytest.mark.asyncio
async def test_await_media_idle_waits_for_flag_release(monkeypatch):
    r = _FakeRedis()
    r.store[GA._BUSY_KEY] = "1"
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_LOCK_POLL_S", 0.01)

    async def release():
        await asyncio.sleep(0.03)
        r.store.pop(GA._BUSY_KEY, None)

    rel = asyncio.create_task(release())
    await GA.await_media_idle(max_wait_s=5.0)
    await rel


@pytest.mark.asyncio
async def test_media_gpu_lock_redis_down_degrades(monkeypatch):
    _patch_redis(monkeypatch, raise_exc=True)
    async with GA.media_gpu_lock():
        pass  # no-op yield, no raise


@pytest.mark.asyncio
async def test_media_gpu_lock_acquire_release(monkeypatch):
    r = _FakeRedis()
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_LOCK_POLL_S", 0.01)
    _stub_dmr_and_httpx(monkeypatch)

    async with GA.media_gpu_lock(acquire_timeout_s=2.0):
        assert GA._LOCK_KEY in r.store
        assert GA._BUSY_KEY in r.store
    # released: token-matched delete ran
    assert GA._LOCK_KEY not in r.store
    assert GA._BUSY_KEY not in r.store


@pytest.mark.asyncio
async def test_media_gpu_lock_timeout(monkeypatch):
    r = _FakeRedis()
    r.store[GA._LOCK_KEY] = "other-token"  # held by someone else
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_LOCK_POLL_S", 0.01)
    with pytest.raises(TimeoutError):
        async with GA.media_gpu_lock(acquire_timeout_s=0.05):
            pass


@pytest.mark.asyncio
async def test_media_gpu_lock_keeps_foreign_lock_on_exit(monkeypatch):
    r = _FakeRedis()
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_LOCK_POLL_S", 0.01)
    _stub_dmr_and_httpx(monkeypatch)

    async with GA.media_gpu_lock(acquire_timeout_s=2.0):
        # TTL "expired" and another job took over mid-hold
        r.store[GA._LOCK_KEY] = "foreign-token"
    assert r.store[GA._LOCK_KEY] == "foreign-token"  # not deleted
    assert GA._BUSY_KEY not in r.store  # busy flag always cleared


@pytest.mark.asyncio
async def test_dmr_slot_acquire_and_release(monkeypatch):
    r = _FakeRedis()
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_DMR_LOCK_ACQUIRE_S", 0.5)
    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())

    async with GA.dmr_slot():
        assert GA._DMR_LOCK_KEY in r.store
    assert GA._DMR_LOCK_KEY not in r.store
    GA.await_media_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_dmr_slot_timeout_proceeds_unserialized(monkeypatch):
    r = _FakeRedis()
    r.store[GA._DMR_LOCK_KEY] = "held"
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(GA, "_DMR_LOCK_ACQUIRE_S", 0.02)
    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())

    # shorten the internal 1.0s poll
    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        await orig_sleep(min(s, 0.005))

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    async with GA.dmr_slot():
        pass
    # never acquired → foreign key untouched
    assert r.store[GA._DMR_LOCK_KEY] == "held"


@pytest.mark.asyncio
async def test_dmr_slot_redis_down_noop(monkeypatch):
    _patch_redis(monkeypatch, raise_exc=True)
    async with GA.dmr_slot():
        pass


@pytest.mark.asyncio
async def test_dmr_slot_set_raises_fail_open(monkeypatch):
    class _BoomSet(_FakeRedis):
        async def set(self, *a, **k):
            raise ConnectionError("name or service not known")

    _patch_redis(monkeypatch, _BoomSet())
    async with GA.dmr_slot():
        pass


@pytest.mark.asyncio
async def test_media_gpu_lock_set_raises_fail_open(monkeypatch):
    class _BoomSet(_FakeRedis):
        async def set(self, *a, **k):
            raise ConnectionError("name or service not known")

    _patch_redis(monkeypatch, _BoomSet())
    async with GA.media_gpu_lock():
        pass


@pytest.mark.asyncio
async def test_dmr_slot_for_media_skips_busy_recheck(monkeypatch):
    r = _FakeRedis()
    _patch_redis(monkeypatch, r)
    idle = AsyncMock()
    monkeypatch.setattr(GA, "await_media_idle", idle)
    async with GA.dmr_slot(_for_media=True):
        pass
    idle.assert_not_awaited()
