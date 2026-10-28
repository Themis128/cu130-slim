"""Unit tests for login lockout + refresh-token rotation helpers in auth.py.

Redis is faked in-memory; helpers are designed to fail open so outage cases
are covered too. No network, no database.
"""
import time

import pytest
from fastapi import HTTPException

from app.api import auth as auth_module
from app.core.security import create_refresh_token, decode_token


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex:
            self.ttls[key] = ex

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key) or 0) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _client():
        return fake

    monkeypatch.setattr(auth_module, "_redis_client", _client)
    return fake


def test_refresh_tokens_get_unique_jti():
    t1 = decode_token(create_refresh_token({"sub": "u1"}))
    t2 = decode_token(create_refresh_token({"sub": "u1"}))
    assert t1["type"] == "refresh"
    assert t1["jti"] and t2["jti"] and t1["jti"] != t2["jti"]


@pytest.mark.asyncio
async def test_lockout_triggers_after_max_failures(fake_redis):
    email = "victim@example.com"
    for _ in range(auth_module._LOGIN_MAX_FAILURES - 1):
        await auth_module._record_login_failure(email)
    assert await auth_module._login_lockout_remaining(email) == 0

    await auth_module._record_login_failure(email)
    remaining = await auth_module._login_lockout_remaining(email)
    assert remaining == auth_module._LOGIN_LOCKOUT_S


@pytest.mark.asyncio
async def test_successful_login_clears_counter(fake_redis):
    email = "victim@example.com"
    for _ in range(auth_module._LOGIN_MAX_FAILURES):
        await auth_module._record_login_failure(email)
    await auth_module._clear_login_failures(email)
    assert await auth_module._login_lockout_remaining(email) == 0


@pytest.mark.asyncio
async def test_lockout_fails_open_on_redis_outage(monkeypatch):
    async def _down():
        raise ConnectionError("redis down")

    monkeypatch.setattr(auth_module, "_redis_client", _down)
    assert await auth_module._login_lockout_remaining("x@y.z") == 0
    await auth_module._record_login_failure("x@y.z")  # must not raise
    await auth_module._clear_login_failures("x@y.z")  # must not raise


@pytest.mark.asyncio
async def test_rotated_refresh_replays_same_pair_in_grace(fake_redis):
    pair = auth_module.TokenResponse(access_token="new-access", refresh_token="new-refresh")
    exp = int(time.time()) + 3600
    await auth_module._record_refresh_rotation("jti-1", pair, exp)

    replayed = await auth_module._replay_rotated_refresh("jti-1")
    assert replayed is not None
    assert replayed.access_token == "new-access"
    assert replayed.refresh_token == "new-refresh"
    # Burned marker outlives the grace mapping
    assert fake_redis.ttls["auth:rt_used:jti-1"] > auth_module._REFRESH_GRACE_S


@pytest.mark.asyncio
async def test_burned_jti_rejected_after_grace(fake_redis):
    fake_redis.store["auth:rt_used:jti-2"] = "1"  # grace key already expired
    with pytest.raises(HTTPException) as exc:
        await auth_module._replay_rotated_refresh("jti-2")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_fresh_jti_returns_none(fake_redis):
    assert await auth_module._replay_rotated_refresh("jti-never-seen") is None
