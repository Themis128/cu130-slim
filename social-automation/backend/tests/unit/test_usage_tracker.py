"""Unit tests for app/services/usage_tracker.py.

DB sessions are faked via the module's async_sessionmaker; the logging
pipeline, estimators, and quota alerting run for real.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.usage_tracker as ut

# ── pure helpers ────────────────────────────────────────────────────


def test_sanitize_log_text():
    assert ut._sanitize_log_text("a\nb\rc") == "a\\nb\\rc"
    assert ut._sanitize_log_text("x\x00\x1f\ty") == "x\ty"
    assert len(ut._sanitize_log_text("z" * 1000)) == 400


def test_estimate_tokens():
    assert ut._estimate_tokens("") == 1
    assert ut._estimate_tokens("one two three") == 3
    assert ut._estimate_tokens("w " * 100) >= 130


def test_estimate_neurons():
    assert ut._estimate_neurons("dmr", "any", "hi") is None
    assert ut._estimate_neurons("cloudflare", "@cf/black-forest/flux-1-schnell", "hi") == 2000
    assert ut._estimate_neurons("cloudflare", "ai/llama3.2", "hi there") == max(50, 2 * ut._estimate_tokens("hi there"))
    assert ut._estimate_neurons("cloudflare", "llama", "w " * 1000) == 500  # capped
    assert ut._estimate_neurons("cloudflare", "unknown-model", "x") is None


# ── track_inference ─────────────────────────────────────────────────


class _FakeSession:
    def __init__(self):
        self.added = []
        self.results = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def execute(self, stmt):
        return self.results.pop(0)


@pytest.fixture
def sess(monkeypatch):
    s = _FakeSession()
    monkeypatch.setattr(ut, "_usage_session", lambda: s)
    return s


@pytest.mark.asyncio
async def test_track_inference_happy(sess):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    await ut.track_inference(
        "cloudflare", "ai/llama3.2", "hello world",
        team_id=tid, user_id=uid, endpoint="/gen",
        latency_ms=12, success=True, estimated_cost=0.001,
        meta_data={"k": uid, "other": 1},
    )
    assert len(sess.added) == 1
    log = sess.added[0]
    assert log.provider == "cloudflare" and log.model == "ai/llama3.2"
    assert log.prompt_length == 11 and log.estimated_tokens == 2
    assert log.estimated_neurons == 50  # llama floor: max(50, tokens*2)
    assert log.meta_data["k"] == str(uid)  # UUID → str
    assert log.meta_data["other"] == 1


@pytest.mark.asyncio
async def test_track_inference_swallows_db_errors(monkeypatch):
    class _Bad:
        async def __aenter__(self):
            raise RuntimeError("db down")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ut, "_usage_session", lambda: _Bad())
    # Must not raise — logging failure never breaks inference
    await ut.track_inference("x", "y", "z")


@pytest.mark.asyncio
async def test_track_inference_quota_check_triggered(sess, monkeypatch):
    chk = AsyncMock()
    monkeypatch.setattr(ut, "_check_daily_quota", chk)
    await ut.track_inference("cloudflare", "m", "p",
                             team_id=uuid.uuid4(), actual_neurons=10)
    chk.assert_awaited_once()
    chk.reset_mock()
    # No neurons or no team → no quota check
    await ut.track_inference("cloudflare", "m", "p", team_id=uuid.uuid4())
    await ut.track_inference("cloudflare", "m", "p", actual_neurons=5)
    chk.assert_not_awaited()


# ── get_daily_neuron_usage ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_usage(sess):
    r = MagicMock()
    r.scalar.return_value = 77
    sess.results.append(r)
    assert await ut.get_daily_neuron_usage(uuid.uuid4()) == 77


@pytest.mark.asyncio
async def test_daily_usage_none_and_error(sess, monkeypatch):
    r = MagicMock()
    r.scalar.return_value = None
    sess.results.append(r)
    assert await ut.get_daily_neuron_usage(uuid.uuid4()) == 0

    class _Bad:
        async def __aenter__(self):
            raise RuntimeError("x")
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ut, "_usage_session", lambda: _Bad())
    assert await ut.get_daily_neuron_usage(uuid.uuid4()) == 0


# ── _check_daily_quota ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quota_skips_non_cloudflare():
    # early return before touching DB
    await ut._check_daily_quota(uuid.uuid4(), "dmr", 100)
    await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 0)


@pytest.mark.asyncio
async def test_quota_no_budget(sess):
    r = MagicMock()
    r.scalar.return_value = None
    sess.results.append(r)
    await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 10)
    r2 = MagicMock()
    r2.scalar.return_value = 0
    sess.results.append(r2)
    await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 10)


@pytest.mark.asyncio
async def test_quota_alert_over_threshold(sess, caplog):
    import logging
    budget = MagicMock()
    budget.scalar.return_value = 100
    total = MagicMock()
    total.scalar.return_value = 85
    sess.results.extend([budget, total])
    with caplog.at_level(logging.WARNING):
        await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 90)
    assert any("budget alert" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_quota_under_threshold_no_alert(sess, caplog):
    import logging
    budget = MagicMock()
    budget.scalar.return_value = 100
    total = MagicMock()
    total.scalar.return_value = 50
    sess.results.extend([budget, total])
    with caplog.at_level(logging.WARNING):
        await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 10)
    assert not any("budget alert" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_quota_db_error_swallowed(monkeypatch):
    class _Bad:
        async def __aenter__(self):
            raise RuntimeError("db")
        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(ut, "_usage_session", lambda: _Bad())
    await ut._check_daily_quota(uuid.uuid4(), "cloudflare", 10)  # no raise
