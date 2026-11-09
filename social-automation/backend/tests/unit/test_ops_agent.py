"""Unit tests for app/services/ops_agent.py — deterministic pieces only.

The LLM verdict and DB session are mocked/heavy; these cover the parts that
must never regress: fingerprint stability, verdict parsing, and the
whitelisted-action allowlist that bounds agent autonomy.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from celery.exceptions import Retry

from app.models.ops_incident import OpsIncident
from app.services import ops_agent as oa


def test_fingerprint_stable_and_scoped():
    a = oa.fingerprint("compose-watchdog", "container-down", "social-api")
    b = oa.fingerprint("compose-watchdog", "container-down", "social-api")
    c = oa.fingerprint("compose-watchdog", "container-down", "social-worker-default")
    assert a == b
    assert a != c
    assert len(a) == 32


def test_parse_verdict_extracts_json_from_prose():
    text = 'Sure! Here you go:\n{"severity":"ops","hypothesis":"x","action":"none","confidence":0.5,"summary":"y"}\nDone.'
    v = oa._parse_verdict(text)
    assert v["action"] == "none"
    assert v["severity"] == "ops"


def test_parse_verdict_garbage_returns_empty():
    assert oa._parse_verdict("no json here") == {}
    assert oa._parse_verdict("") == {}
    assert oa._parse_verdict("{broken") == {}


def test_rerun_allowlist_only_readonly_tasks():
    # Every whitelisted target must exist as a real task path and none may
    # point at publishing/mutation lanes.
    assert set(oa.ALLOWED_RERUN_TASKS) == {"dmr_health", "devapps_sync", "analytics_sync"}
    for path in oa.ALLOWED_RERUN_TASKS.values():
        assert path.startswith("app.worker.tasks.")
        assert "publish" not in path
        assert "send" not in path


def test_incident_fingerprint_length_matches_index():
    inc = OpsIncident(fingerprint="x" * 32, source="s", kind="k", title="t")
    assert len(inc.fingerprint) <= 64


class _SessionCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


class _Redis:
    def __init__(self):
        self.sets = []
        self.closed = False

    async def set(self, key, value):
        self.sets.append((key, value))

    async def aclose(self):
        self.closed = True


def _wire_sweep(monkeypatch, stats=None, redis=None):
    from app.worker.tasks import ops_agent as task

    db = object()
    monkeypatch.setattr(task, "task_session", lambda: _SessionCM(db))
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(oa, "sweep", AsyncMock(return_value=stats or {"triaged": 1}))
    if redis is not None:
        import redis.asyncio as aioredis

        monkeypatch.setattr(aioredis, "from_url", lambda *a, **kw: redis)
        import app.core.config as config

        monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://fake"))
    return task


def test_ops_agent_sweep_task_success_and_heartbeat(monkeypatch):
    redis = _Redis()
    task = _wire_sweep(monkeypatch, stats={"triaged": 2, "actioned": 1}, redis=redis)

    assert task.ops_agent_sweep.run() == {"triaged": 2, "actioned": 1}
    assert redis.sets and redis.sets[0][0] == "ops_agent:last_sweep"
    assert redis.closed


def test_ops_agent_sweep_heartbeat_failure_is_tolerated(monkeypatch):
    import redis.asyncio as aioredis

    def _boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(aioredis, "from_url", _boom)
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://fake"))
    task = _wire_sweep(monkeypatch, stats={"resolved_stale": 1})

    assert task.ops_agent_sweep.run() == {"resolved_stale": 1}


def test_ops_agent_sweep_task_retries_on_failure(monkeypatch):
    from app.worker.tasks import ops_agent as task

    def _boom(coro):
        coro.close()
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(task, "run_async", _boom)
    retried = []

    def _retry(exc=None, **kwargs):
        retried.append(exc)
        raise Retry(exc=exc)

    monkeypatch.setattr(task.ops_agent_sweep, "retry", _retry)
    with pytest.raises(Retry):
        task.ops_agent_sweep.run()
    assert retried and isinstance(retried[0], RuntimeError)
