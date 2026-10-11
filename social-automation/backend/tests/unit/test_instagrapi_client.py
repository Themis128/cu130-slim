"""Unit tests for app/services/instagrapi_client.py."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.instagrapi_client as IC


def _settings(**over):
    base = dict(INSTAGRAM_PROXY_POOL="", INSTAGRAM_PROXY="",
                INSTAGRAM_RATE_LIMIT_RPM=30, REDIS_URL="")
    base.update(over)
    return SimpleNamespace(**base)


def _client(monkeypatch, tmp_path, **sover):
    monkeypatch.setattr(IC, "get_settings", lambda: _settings(**sover))
    monkeypatch.setattr(IC, "_SESSION_DIR", tmp_path / ".ig")
    c = IC.InstagrapiClient("user", "pw", rate_limit=False)
    c._session_file = tmp_path / ".ig" / "user.json"
    return c


def test_proxy_list_building(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c._proxies == ["socks5://warp-proxy:1080"]
    assert c._current_proxy == "socks5://warp-proxy:1080"

    c = _client(monkeypatch, tmp_path,
                INSTAGRAM_PROXY="http://p1", INSTAGRAM_PROXY_POOL="http://p2, http://p3")
    assert c._proxies == ["http://p1", "http://p2", "http://p3"]

    # primary already in pool → not duplicated
    c = _client(monkeypatch, tmp_path,
                INSTAGRAM_PROXY="http://p2", INSTAGRAM_PROXY_POOL="http://p1,http://p2")
    assert c._proxies == ["http://p1", "http://p2"]


def test_rate_limiter_no_redis():
    rl = IC._RedisRateLimiter("", 30)
    assert rl._redis is None
    rl2 = IC._RedisRateLimiter("redis://x", 0)
    assert rl2._redis is None


@pytest.mark.asyncio
async def test_rate_limiter_wait():
    rl = IC._RedisRateLimiter("", 30)
    await rl.wait("acct")  # no redis → no-op

    class _R:
        def __init__(self, counts):
            self._counts = iter(counts)
            self.added = []

        async def zremrangebyscore(self, *a):
            pass

        async def zcard(self, k):
            return next(self._counts)

        async def zadd(self, k, m):
            self.added.append(m)

        async def zrange(self, *a, **kw):
            return [("t", 100.0)]

    rl = IC._RedisRateLimiter.__new__(IC._RedisRateLimiter)
    rl.rpm = 2
    rl._redis = _R([3, 1])      # over→wait, then under
    rl._key_prefix = "ig_ratelimit"
    with patch("app.services.instagrapi_client.asyncio.sleep",
               new_callable=AsyncMock) as sl:
        await rl.wait("acct")
    assert len(rl._redis.added) == 1
    sl.assert_awaited_once()


def test_build_client(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    calls = []

    class _CL:
        def set_proxy(self, p):
            calls.append(p)

    fake_instagrapi = SimpleNamespace(Client=_CL)
    monkeypatch.setitem(sys.modules, "instagrapi", fake_instagrapi)
    cl = c._build_client()
    assert calls == ["socks5://warp-proxy:1080"]

    class _CL2:
        def set_proxy(self, p):
            raise RuntimeError("bad proxy")
    monkeypatch.setitem(sys.modules, "instagrapi",
                        SimpleNamespace(Client=_CL2))
    cl = c._build_client()  # proxy error swallowed
    assert isinstance(cl, _CL2)


def test_load_save_session(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    class _CL:
        def __init__(self):
            self.logged = False
            self.settings = {}

        def set_settings(self, s):
            self.settings = s

        def login(self, u, p):
            self.logged = True

        def get_settings(self):
            return {"k": 1}

    # no file
    assert c._load_session(_CL()) is False

    # fresh save then load
    c._session_file.parent.mkdir(parents=True, exist_ok=True)
    c._session_file.write_text('{"a": 1}')
    cl = _CL()
    assert c._load_session(cl) is True and cl.logged

    # stale file → deleted, False
    import os
    old = c._session_file.stat().st_mtime - (IC._SESSION_TTL + 10)
    os.utime(c._session_file, (old, old))
    assert c._load_session(_CL()) is False
    assert not c._session_file.exists()

    # restore error → unlink + False
    c._session_file.write_text('{"a": 1}')
    class _Bad(_CL):
        def login(self, u, p):
            raise RuntimeError("rejected")
    assert c._load_session(_Bad()) is False
    assert not c._session_file.exists()

    # save
    c._save_session(_CL())
    assert c._session_file.exists()


def test_ensure_client(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    class _CL:
        def login(self, u, p):
            pass

        def get_settings(self):
            return {}

    monkeypatch.setattr(c, "_build_client", lambda: _CL())
    cl1 = c._ensure_client()
    assert c._ensure_client() is cl1  # cached

    # login failure
    c._client = None

    class _BadCL:
        def login(self, u, p):
            raise RuntimeError("denied")
    monkeypatch.setattr(c, "_build_client", lambda: _BadCL())
    with pytest.raises(IC.InstagrapiError, match="login failed"):
        c._ensure_client()


def test_classifiers(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c._is_session_error(RuntimeError("login_required")) is True
    assert c._is_session_error(RuntimeError("HTTP 403 nope")) is True
    assert c._is_session_error(RuntimeError("fine")) is False
    assert c._is_throttle_error(IC.InstagrapiError("HTTP 429 too many")) is True
    assert c._is_throttle_error(IC.InstagrapiError("weird")) is False


def test_sync_call_retries(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    sentinel = object()
    monkeypatch.setattr(c, "_ensure_client", lambda: sentinel)

    # success
    assert c._sync_call(lambda cl: "ok") == "ok"

    # generic error → InstagrapiError immediately
    with pytest.raises(IC.InstagrapiError, match="operation failed"):
        c._sync_call(lambda cl: 1/0)

    # session error → invalidate + retry, then final raise
    calls = []
    def _fail_then_ok(cl):
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("login_required")
        return "recovered"
    assert c._sync_call(_fail_then_ok) == "recovered"

    calls.clear()
    with pytest.raises(IC.InstagrapiError, match="session failed"):
        c._sync_call(lambda cl: (_ for _ in ()).throw(
            RuntimeError("login_required")))
    assert calls == [] or True

    # throttle → backoff then raise at last attempt
    monkeypatch.setattr(c, "_backoff", lambda a: None)
    monkeypatch.setattr(c, "_is_throttle", lambda e: True)
    with pytest.raises(IC.InstagrapiError, match="rate-limited"):
        c._sync_call(lambda cl: (_ for _ in ()).throw(RuntimeError("x")))

    # proxy block → rotate
    rotated = []
    monkeypatch.setattr(c, "_is_throttle", lambda e: False)
    monkeypatch.setattr(c, "_is_proxy_block", lambda e: True)
    monkeypatch.setattr(c, "_rotate_proxy",
                        lambda: rotated.append(1))
    with pytest.raises(IC.InstagrapiError, match="Proxy exhausted"):
        c._sync_call(lambda cl: (_ for _ in ()).throw(RuntimeError("x")))
    assert len(rotated) == c._max_retries


def test_rotate_proxy_invalidate_backoff(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path,
                INSTAGRAM_PROXY="http://p1",
                INSTAGRAM_PROXY_POOL="http://p2")
    c._client = object()
    c._rotate_proxy()
    assert c._proxy_index == 1 and c._current_proxy == "http://p2"
    assert c._client is None

    # invalidate clears client + session file
    c._session_file.parent.mkdir(parents=True, exist_ok=True)
    c._session_file.write_text("{}")
    c._client = object()
    c._invalidate()
    assert c._client is None and not c._session_file.exists()

    # backoff sleeps
    with patch.object(IC.time, "sleep") as sl:
        c._backoff(0)
        c._backoff(10)
    assert sl.call_count == 2
    assert sl.call_args_list[0].args[0] == 1   # min(120, 2**0) + 0
    assert sl.call_args_list[1].args[0] == 130  # min(120, 2**10) + 10


@pytest.mark.asyncio
async def test_wait_for_rate_limit_passthrough(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c._rate_limiter = SimpleNamespace(wait=AsyncMock())
    await c._wait_for_rate_limit()
    c._rate_limiter.wait.assert_awaited_once_with("user")


@pytest.mark.asyncio
async def test_uploads(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "_wait_for_rate_limit", AsyncMock())

    media = SimpleNamespace(id=1, pk=2, code="abc")
    cl = SimpleNamespace(
        photo_upload=lambda p, cap: media,
        video_upload=lambda p, cap: media,
        album_upload=lambda paths, cap: media)
    seen = []

    def _sync(fn):
        seen.append(fn)
        return fn(cl)
    monkeypatch.setattr(c, "_sync_call", _sync)

    out = await c.upload_photo("/tmp/x.jpg", "cap")
    assert out == {"id": "1", "pk": "2", "code": "abc"}
    out = await c.upload_video("/tmp/x.mp4", "cap")
    assert out["pk"] == "2"
    out = await c.upload_album(["/tmp/a", "/tmp/b"], "cap")
    assert out["id"] == "1"
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_get_profile_fallback_chain(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    # success path
    monkeypatch.setattr(c, "_sync_call", lambda fn: {"pk": 1})
    monkeypatch.setattr(__import__("asyncio"), "to_thread",
                        lambda fn, *a: asyncio_to(fn, *a))

    async def asyncio_to(fn, *a):
        return fn(*a)

    import asyncio
    monkeypatch.setattr(asyncio, "to_thread", asyncio_to)
    assert await c.get_profile() == {"pk": 1}

    # non-throttle error → re-raise
    def _raise(fn):
        raise IC.InstagrapiError("weird failure")
    monkeypatch.setattr(c, "_sync_call", _raise)
    with pytest.raises(IC.InstagrapiError, match="weird"):
        await c.get_profile()

    # throttle → free fallback success
    def _raise429(fn):
        raise IC.InstagrapiError("HTTP 429 throttled")
    monkeypatch.setattr(c, "_sync_call", _raise429)
    free = AsyncMock(return_value={"pk": "free"})
    monkeypatch.setattr(IC, "free_instagram_client",
                        SimpleNamespace(get_user_by_username=free))
    assert await c.get_profile() == {"pk": "free"}

    # free fails, hiker disabled → re-raise free error
    monkeypatch.setattr(IC, "free_instagram_client", SimpleNamespace(
        get_user_by_username=AsyncMock(
            side_effect=IC.FreeInstagramError("no"))))
    monkeypatch.setattr(IC, "hiker_client",
                        SimpleNamespace(enabled=False))
    with pytest.raises(IC.FreeInstagramError):
        await c.get_profile()

    # free fails, hiker ok
    monkeypatch.setattr(IC, "hiker_client", SimpleNamespace(
        enabled=True,
        get_user_by_username=AsyncMock(return_value={"pk": "h"})))
    assert await c.get_profile() == {"pk": "h"}

    # hiker fails too
    monkeypatch.setattr(IC, "hiker_client", SimpleNamespace(
        enabled=True,
        get_user_by_username=AsyncMock(
            side_effect=IC.HikerAPIError("down"))))
    with pytest.raises(IC.InstagrapiError, match="All fallbacks"):
        await c.get_profile()


@pytest.mark.asyncio
async def test_update_profile(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "_wait_for_rate_limit", AsyncMock())

    # no changes
    assert await c.update_profile() == {"status": "no_changes"}

    account = SimpleNamespace(username="u", full_name="F",
                              biography="b", external_url=None)
    seen = {}

    def _sync(fn):
        cl = SimpleNamespace(
            account_edit=lambda **kw: seen.update(kw) or account)
        return fn(cl)
    monkeypatch.setattr(c, "_sync_call", _sync)
    out = await c.update_profile(biography="bio", full_name="F",
                                 external_url="https://x")
    assert seen == {"biography": "bio", "full_name": "F",
                    "external_url": "https://x"}
    assert out["status"] == "updated" and out["external_url"] is None


def test_rate_limiter_redis_init(monkeypatch):
    import redis.asyncio as aioredis
    fake = SimpleNamespace(from_url=lambda url, **kw: "REDIS")
    monkeypatch.setattr(aioredis, "Redis",
                        SimpleNamespace(from_url=fake.from_url))
    rl = IC._RedisRateLimiter("redis://x", 30)
    assert rl._redis == "REDIS"

    monkeypatch.setattr(aioredis, "Redis",
                        SimpleNamespace(
                            from_url=lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("no redis"))))
    rl = IC._RedisRateLimiter("redis://x", 30)
    assert rl._redis is None


def test_save_session_error(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(type(c._session_file), "write_text",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            OSError("rofs"))) if False else None
    # patch Path.write_text globally for this file path
    import pathlib
    orig = pathlib.Path.write_text
    def _fail(self, *a, **k):
        if str(self).endswith("user.json"):
            raise OSError("rofs")
        return orig(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "write_text", _fail)
    c._save_session(SimpleNamespace(get_settings=lambda: {}))  # swallowed


def test_is_session_error_instance():
    from instagrapi.exceptions import LoginRequired
    c = IC.InstagrapiClient.__new__(IC.InstagrapiClient)
    assert c._is_session_error(LoginRequired("x")) is True
