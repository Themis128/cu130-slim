"""Tests for app/services/stack_ops.py — stack-ops sleeper integration."""
from __future__ import annotations

import time

import pytest

import app.services.stack_ops as stack_ops


class _FakeHTTP:
    """httpx.AsyncClient replacement — routes GET/POST to canned responses."""

    def __init__(self, *, get=None, post=None, get_exc=None, post_exc=None):
        self._get = get
        self._post = post
        self._get_exc = get_exc
        self._post_exc = post_exc
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if self._get_exc:
            raise self._get_exc
        return self._get

    async def post(self, url, **kw):
        self.posts.append(url)
        if self._post_exc:
            raise self._post_exc
        return self._post


class _Resp:
    def __init__(self, code=200, data=None):
        self.status_code = code
        self._data = data or {}

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(stack_ops, "_cache", (0.0, {}))
    yield


def _patch_http(monkeypatch, client):
    monkeypatch.setattr(
        stack_ops.httpx, "AsyncClient", lambda *a, **kw: client
    )


@pytest.mark.asyncio
async def test_statuses_ok_and_cache(monkeypatch):
    data = {"comfyui": {"state": "stopped", "container": "social-media-comfyui-gpu"}}
    fake = _FakeHTTP(get=_Resp(200, data))
    _patch_http(monkeypatch, fake)
    out = await stack_ops.statuses()
    assert out == data
    # second call within TTL — uses cache even though the fake now raises
    fake._get_exc = RuntimeError("down")
    out2 = await stack_ops.statuses()
    assert out2 == data


@pytest.mark.asyncio
async def test_statuses_non_200_and_exception(monkeypatch):
    _patch_http(monkeypatch, _FakeHTTP(get=_Resp(500)))
    assert await stack_ops.statuses() == {}
    _patch_http(monkeypatch, _FakeHTTP(get_exc=RuntimeError("x")))
    assert await stack_ops.statuses() == {}


@pytest.mark.asyncio
async def test_statuses_stale_cache_returned_on_failure(monkeypatch):
    # Seed a stale cache entry — TTL expired but data survives failures.
    monkeypatch.setattr(
        stack_ops, "_cache", (time.monotonic() - 999, {"svc": {"state": "running"}})
    )
    _patch_http(monkeypatch, _FakeHTTP(get_exc=RuntimeError("x")))
    assert await stack_ops.statuses() == {"svc": {"state": "running"}}


@pytest.mark.asyncio
async def test_service_state_by_key_and_container(monkeypatch):
    data = {
        "comfyui": {"state": "stopped", "container": "social-media-comfyui-gpu"},
        "ollama": {"state": "running", "container": "social-ollama"},
    }
    _patch_http(monkeypatch, _FakeHTTP(get=_Resp(200, data)))
    assert await stack_ops.service_state("comfyui") == "stopped"
    # look up by container name
    assert await stack_ops.service_state("social-ollama") == "running"
    # unknown
    assert await stack_ops.service_state("nope") is None


@pytest.mark.asyncio
async def test_service_state_stack_ops_absent(monkeypatch):
    _patch_http(monkeypatch, _FakeHTTP(get_exc=RuntimeError("x")))
    assert await stack_ops.service_state("comfyui") is None
    assert await stack_ops.is_asleep("comfyui") is False


@pytest.mark.asyncio
async def test_is_asleep(monkeypatch):
    data = {"svc": {"state": "stopped", "container": "c1"}}
    _patch_http(monkeypatch, _FakeHTTP(get=_Resp(200, data)))
    assert await stack_ops.is_asleep("c1") is True
    assert await stack_ops.is_asleep("svc") is True


@pytest.mark.asyncio
async def test_wake(monkeypatch):
    fake = _FakeHTTP(post=_Resp(200))
    _patch_http(monkeypatch, fake)
    assert await stack_ops.wake("comfyui") is True
    assert fake.posts[0].endswith("/wake/comfyui")

    _patch_http(monkeypatch, _FakeHTTP(post=_Resp(500)))
    assert await stack_ops.wake("comfyui") is False

    _patch_http(monkeypatch, _FakeHTTP(post_exc=RuntimeError("x")))
    assert await stack_ops.wake("comfyui") is False


@pytest.mark.asyncio
async def test_keepawake(monkeypatch):
    fake = _FakeHTTP(post=_Resp(200))
    _patch_http(monkeypatch, fake)
    assert await stack_ops.keepawake("comfyui", ttl_s=120) is True
    assert "ttl=120" in fake.posts[0]

    _patch_http(monkeypatch, _FakeHTTP(post=_Resp(404)))
    assert await stack_ops.keepawake("comfyui") is False

    _patch_http(monkeypatch, _FakeHTTP(post_exc=RuntimeError("x")))
    assert await stack_ops.keepawake("comfyui") is False
