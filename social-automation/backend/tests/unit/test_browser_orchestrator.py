"""Coverage for app/services/browser_orchestrator.py."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.browser_orchestrator as BO


class _Redis:
    """In-memory fake of the redis.asyncio API surface used here."""

    def __init__(self):
        self.store = {}
        self.queue = []
        self.fail = {}  # method name -> exception

    def _maybe_fail(self, name):
        exc = self.fail.get(name)
        if exc is not None:
            if isinstance(exc, list):
                exc = exc.pop(0)
                if exc is None:
                    return
            raise exc

    async def rpush(self, k, v):
        self._maybe_fail("rpush")
        self.queue.append(v)
        return len(self.queue)

    async def lindex(self, k, i):
        self._maybe_fail("lindex")
        try:
            return self.queue[i]
        except IndexError:
            return None

    async def lpop(self, k):
        self._maybe_fail("lpop")
        return self.queue.pop(0) if self.queue else None

    async def lrem(self, k, count, v):
        self._maybe_fail("lrem")
        self.queue = [x for x in self.queue if x != v]
        return 1

    async def llen(self, k):
        self._maybe_fail("llen")
        return len(self.queue)

    async def set(self, k, v, nx=False, ex=None):
        self._maybe_fail("set")
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    async def get(self, k):
        self._maybe_fail("get")
        return self.store.get(k)

    async def expire(self, k, t):
        self._maybe_fail("expire")
        return 1

    async def delete(self, *ks):
        self._maybe_fail("delete")
        for k in ks:
            self.store.pop(k, None)
        self.queue.clear()
        return 1


def _redis(monkeypatch, r=None):
    r = r or _Redis()
    monkeypatch.setattr(BO, "_get_redis", AsyncMock(return_value=r))
    return r


class TestGetRedis:
    @pytest.mark.asyncio
    async def test_from_url(self, monkeypatch):
        import redis.asyncio as aioredis

        called = {}
        monkeypatch.setattr(aioredis, "from_url", lambda url, **kw: called.setdefault("url", url))
        monkeypatch.setattr(BO, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://r:6379/0"))
        await BO._get_redis()
        assert called["url"] == "redis://r:6379/0"


class TestEnter:
    @pytest.mark.asyncio
    async def test_redis_down_best_effort(self, monkeypatch):
        monkeypatch.setattr(BO, "_get_redis", AsyncMock(side_effect=OSError("no redis")))
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert not s._acquired

    @pytest.mark.asyncio
    async def test_acquire_immediately(self, monkeypatch):
        r = _redis(monkeypatch)
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired
        assert r.store[BO._LOCK_KEY] == s._lock_token
        assert r.store[BO._PLATFORM_KEY] == "ig"

    @pytest.mark.asyncio
    async def test_evicts_stale_front(self, monkeypatch):
        r = _redis(monkeypatch)
        # dead token from 10 min ago + a malformed token
        r.queue.append(f"dead:{time.time() - 400}")
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired
        # only our token remains in queue (dead one evicted)
        assert r.queue == [s._lock_token]

    @pytest.mark.asyncio
    async def test_malformed_front_token(self, monkeypatch):
        r = _redis(monkeypatch)
        r.queue.append("notoken")  # no colon → ts 0 → stale → evicted
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired

    @pytest.mark.asyncio
    async def test_fresh_front_waits(self, monkeypatch):
        r = _redis(monkeypatch)
        r.queue.append(f"other:{time.time()}")
        s = BO.BrowserSession("ig", "BRIDGE", max_wait=0.5)
        monkeypatch.setattr(BO.asyncio, "sleep", AsyncMock())
        assert await s.__aenter__() == "BRIDGE"
        assert not s._acquired  # timed out, best effort
        assert s._lock_token not in r.queue  # removed from queue

    @pytest.mark.asyncio
    async def test_front_but_lock_held_then_gets_it(self, monkeypatch):
        r = _redis(monkeypatch)
        calls = {"n": 0}
        orig_set = r.set

        async def flaky_set(k, v, nx=False, ex=None):
            calls["n"] += 1
            if calls["n"] == 1:
                r.store[BO._LOCK_KEY] = "someone:else"
            out = await orig_set(k, v, nx=nx, ex=ex)
            if calls["n"] == 1:
                r.store.pop(BO._LOCK_KEY, None)
            return out

        r.set = flaky_set
        s = BO.BrowserSession("ig", "BRIDGE")
        monkeypatch.setattr(BO.asyncio, "sleep", AsyncMock())
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired

    @pytest.mark.asyncio
    async def test_empty_queue_eviction_break(self, monkeypatch):
        r = _redis(monkeypatch)
        calls = {"n": 0}
        orig = r.lindex

        async def lindex(k, i):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # queue transiently empty during eviction check
            return await orig(k, i)

        r.lindex = lindex
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired

    @pytest.mark.asyncio
    async def test_acquire_exception_best_effort(self, monkeypatch):
        r = _redis(monkeypatch)
        r.fail["lindex"] = OSError("lindex down")
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert not s._acquired

    @pytest.mark.asyncio
    async def test_acquire_exception_cleanup_fails(self, monkeypatch):
        r = _redis(monkeypatch)
        r.fail["lindex"] = OSError("down")
        r.fail["lrem"] = OSError("also down")
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"

    @pytest.mark.asyncio
    async def test_long_wait_logs_info(self, monkeypatch):
        _redis(monkeypatch)
        ticks = iter([0.0, 0.0, 0.0, 2.5, 2.5])
        monkeypatch.setattr(BO.time, "perf_counter", lambda: next(ticks, 9.9))
        s = BO.BrowserSession("ig", "BRIDGE")
        assert await s.__aenter__() == "BRIDGE"
        assert s._acquired


class TestRenew:
    @pytest.mark.asyncio
    async def test_not_acquired(self):
        s = BO.BrowserSession("ig", "b")
        assert await s.renew() is False

    @pytest.mark.asyncio
    async def test_renews(self, monkeypatch):
        r = _redis(monkeypatch)
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        r.store[BO._LOCK_KEY] = "ig:1"
        assert await s.renew() is True

    @pytest.mark.asyncio
    async def test_token_mismatch(self, monkeypatch):
        r = _redis(monkeypatch)
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        r.store[BO._LOCK_KEY] = "other:2"
        assert await s.renew() is False

    @pytest.mark.asyncio
    async def test_exception(self, monkeypatch):
        monkeypatch.setattr(BO, "_get_redis", AsyncMock(side_effect=OSError("x")))
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        assert await s.renew() is False


class TestExit:
    @pytest.mark.asyncio
    async def test_not_acquired_noop(self, monkeypatch):
        _redis(monkeypatch)
        s = BO.BrowserSession("ig", "b")
        await s.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_release_held(self, monkeypatch):
        r = _redis(monkeypatch)
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        r.store[BO._LOCK_KEY] = "ig:1"
        r.store[BO._PLATFORM_KEY] = "ig"
        r.queue.append("ig:1")
        await s.__aexit__(None, None, None)
        assert BO._LOCK_KEY not in r.store
        assert BO._PLATFORM_KEY not in r.store
        assert "ig:1" not in r.queue

    @pytest.mark.asyncio
    async def test_token_mismatch_keeps_lock(self, monkeypatch):
        r = _redis(monkeypatch)
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        r.store[BO._LOCK_KEY] = "new:owner"
        r.queue.append("ig:1")
        await s.__aexit__(None, None, None)
        assert r.store[BO._LOCK_KEY] == "new:owner"
        assert "ig:1" not in r.queue

    @pytest.mark.asyncio
    async def test_release_exception_swallowed(self, monkeypatch):
        r = _redis(monkeypatch)
        r.fail["get"] = OSError("down")
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        s._acquired = True
        await s.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_lrem_exception_swallowed(self, monkeypatch):
        r = _redis(monkeypatch)
        r.fail["lrem"] = OSError("down")
        s = BO.BrowserSession("ig", "b")
        s._lock_token = "ig:1"
        await s._remove_from_queue(r)  # no raise


class TestHelpers:
    def test_factory(self):
        s = BO.browser_session("threads", "B", max_wait=5)
        assert isinstance(s, BO.BrowserSession)
        assert s.platform == "threads"
        assert s.max_wait == 5

    @pytest.mark.asyncio
    async def test_current_platform(self, monkeypatch):
        r = _redis(monkeypatch)
        r.store[BO._PLATFORM_KEY] = "tiktok"
        assert await BO.get_current_platform() == "tiktok"

    @pytest.mark.asyncio
    async def test_current_platform_error(self, monkeypatch):
        monkeypatch.setattr(BO, "_get_redis", AsyncMock(side_effect=OSError("x")))
        assert await BO.get_current_platform() is None

    @pytest.mark.asyncio
    async def test_queue_length(self, monkeypatch):
        r = _redis(monkeypatch)
        r.queue = ["a", "b", "c"]
        assert await BO.get_queue_length() == 3

    @pytest.mark.asyncio
    async def test_queue_length_error(self, monkeypatch):
        monkeypatch.setattr(BO, "_get_redis", AsyncMock(side_effect=OSError("x")))
        assert await BO.get_queue_length() == 0

    @pytest.mark.asyncio
    async def test_force_release(self, monkeypatch):
        r = _redis(monkeypatch)
        r.store[BO._LOCK_KEY] = "x"
        r.queue = ["a"]
        assert await BO.force_release_lock() is True
        assert not r.store and not r.queue

    @pytest.mark.asyncio
    async def test_force_release_error(self, monkeypatch):
        monkeypatch.setattr(BO, "_get_redis", AsyncMock(side_effect=OSError("x")))
        assert await BO.force_release_lock() is False
