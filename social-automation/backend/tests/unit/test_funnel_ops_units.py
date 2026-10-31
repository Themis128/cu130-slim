"""Unit tests: loop_harness, mlflow_track, funnel_score, funnel_sync."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import loop_harness as LH
from app.services import mlflow_track as MT
from app.worker.tasks import funnel_score as FS
from app.worker.tasks import funnel_sync as FY

# ── loop_harness ────────────────────────────────────────────────────────────


@pytest.fixture
def dmr(monkeypatch):
    import app.services.dmr as dmr_mod

    m = AsyncMock()
    monkeypatch.setattr(dmr_mod, "call_dmr_chat", m)
    return m


@pytest.mark.asyncio
async def test_llm_call_dict_response_and_trim(dmr):
    dmr.return_value = {"text": "  answer  "}
    out = await LH.llm_call("x" * 50, system="sys", model="m", trim_chars=10)
    assert out == "answer"
    args, kwargs = dmr.await_args
    assert args[0] == "x" * 10 and kwargs == {"system": "sys", "model_override": "m"}


@pytest.mark.asyncio
async def test_llm_call_str_response_retries_on_empty_and_expect(dmr):
    dmr.side_effect = ["", "short", "long enough text"]
    out = await LH.llm_call("p", expect=lambda t: len(t) > 6)
    assert out == "long enough text" and dmr.await_count == 3


@pytest.mark.asyncio
async def test_llm_call_exhausted_returns_empty(dmr):
    dmr.return_value = {"text": None}
    assert await LH.llm_call("p", max_retries=2) == ""


class _HttpClient:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.posts = payload, exc, []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if self.exc:
            raise self.exc
        return SimpleNamespace(json=lambda: self.payload)


@pytest.mark.asyncio
async def test_llm_call_falls_back_to_bare_chat(dmr, monkeypatch):
    import httpx

    dmr.side_effect = RuntimeError("schema hang")
    client = _HttpClient({"choices": [{"message": {"content": "fallback"}}]})
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("DMR_URL", "http://dmr")
    monkeypatch.delenv("DMR_TEXT_MODEL", raising=False)
    assert await LH.llm_call("p", system="s") == "fallback"
    url, body = client.posts[0]
    assert url == "http://dmr/chat/completions"
    assert body["model"] == "docker.io/ai/smollm2:latest"
    assert body["messages"][0] == {"role": "system", "content": "s"}

    client.payload = {"choices": []}
    client.posts.clear()
    assert await LH.llm_call("p", model="mm", max_retries=1) == ""
    assert client.posts[0][1]["model"] == "mm" and len(client.posts[0][1]["messages"]) == 1


@pytest.mark.asyncio
async def test_llm_call_transport_error_reraised(dmr, monkeypatch):
    import httpx

    dmr.side_effect = RuntimeError("svc")
    monkeypatch.setattr(httpx, "AsyncClient", _HttpClient(exc=ConnectionError("down")))
    with pytest.raises(ConnectionError):
        await LH.llm_call("p", max_retries=2)


def test_single_job_prompt():
    assert LH.single_job_prompt("write a subject") == "Your only job: write a subject."
    assert LH.single_job_prompt("j", context="ctx", constraint="short") == "Your only job: j.\nConstraint: short.\nctx"


# ── mlflow_track ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_mlflow(monkeypatch):
    m = types.ModuleType("mlflow")
    m.calls = []
    m.set_tracking_uri = lambda uri: m.calls.append(("uri", uri))
    m.set_experiment = lambda name: m.calls.append(("exp", name))
    m.log_metric = lambda k, v: m.calls.append(("metric", k, v))
    m.log_param = lambda k, v: m.calls.append(("param", k, v))

    class _Run:
        def __init__(self, run_name=None, tags=None):
            m.calls.append(("run", run_name, tags))

        def __enter__(self):
            return "RUN"

        def __exit__(self, *a):
            return False

    m.start_run = _Run
    monkeypatch.setitem(sys.modules, "mlflow", m)
    return m


def test_mlflow_run_context(fake_mlflow):
    with MT.run("exp1", run_name="r") as (m, r):
        assert m is fake_mlflow and r == "RUN"
    assert fake_mlflow.calls[:3] == [("uri", MT._URI), ("exp", "exp1"), ("run", "r", {})]


def test_log_funnel_score(fake_mlflow):
    MT.log_funnel_score({"hot": 2, "cold": 1}, 3)
    metrics = [c[1:] for c in fake_mlflow.calls if c[0] == "metric"]
    assert metrics == [("leads_hot", 2), ("leads_cold", 1), ("leads_total", 3)]


def test_log_content_eval(fake_mlflow):
    MT.log_content_eval("p1", 88.0, "qwen", {"hook": 9.0})
    assert ("param", "post_id", "p1") in fake_mlflow.calls
    assert ("metric", "score_hook", 9.0) in fake_mlflow.calls
    fake_mlflow.calls.clear()
    MT.log_content_eval("p2", 50.0, "qwen")
    assert [c for c in fake_mlflow.calls if c[0] == "metric"] == [("metric", "score_total", 50.0)]


def test_log_dmr_usage(fake_mlflow):
    MT.log_dmr_usage("chat", "m", 12.5, True)
    MT.log_dmr_usage("chat", "m", 99.0, False)
    oks = [c[2] for c in fake_mlflow.calls if c[0] == "metric" and c[1] == "ok"]
    assert oks == [1, 0]


# ── funnel_score ────────────────────────────────────────────────────────────


def _session(rows):
    s = MagicMock()
    res = MagicMock()
    res.fetchall.return_value = rows
    s.execute = AsyncMock(return_value=res)
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    return s


def _lead(interest=None, email=None, thread_id=None, updated_at=None):
    return SimpleNamespace(id=1, email=email, source="x", interest=interest, thread_id=thread_id, updated_at=updated_at)


@pytest.mark.asyncio
async def test_funnel_score_tiers(monkeypatch):
    import app.services.mlflow_track as mt
    import app.worker._async as wa

    old = datetime.now(UTC) - timedelta(days=30)
    rows = [
        _lead(interest="audit"),
        _lead(thread_id="t1", updated_at=old),
        _lead(updated_at=datetime.now(UTC)),
        _lead(interest="pricing", email="a@b.c", updated_at=old),
        _lead(interest="pricing"),
        _lead(),
    ]
    monkeypatch.setattr(wa, "get_session_factory", lambda: (lambda: _session(rows)))
    logged = []
    monkeypatch.setattr(mt, "log_funnel_score", lambda s, t: logged.append((dict(s), t)))
    out = await FS._score()
    assert out == {"scored": {"hot": 3, "warm": 1, "cold": 2}, "total": 6}
    assert logged == [({"hot": 3, "warm": 1, "cold": 2}, 6)]


@pytest.mark.asyncio
async def test_funnel_score_mlflow_failure_ignored(monkeypatch):
    import app.services.mlflow_track as mt
    import app.worker._async as wa

    monkeypatch.setattr(wa, "get_session_factory", lambda: (lambda: _session([])))

    def boom(*a):
        raise RuntimeError("no mlflow")

    monkeypatch.setattr(mt, "log_funnel_score", boom)
    assert await FS._score() == {"scored": {"hot": 0, "warm": 0, "cold": 0}, "total": 0}


def test_funnel_score_task(monkeypatch):
    monkeypatch.setattr(FS, "_score", AsyncMock(return_value={"total": 0}))
    monkeypatch.setattr(FS, "run_async", lambda c: asyncio.run(c))
    assert FS.run_funnel_score.run() == {"total": 0}


# ── funnel_sync ─────────────────────────────────────────────────────────────


def test_funnel_sync_run_and_dbt(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(returncode=0 if cmd[0] == "python" else 2, stdout="o" * 400, stderr="e" * 200)

    monkeypatch.setattr(FY.subprocess, "run", fake_run)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:s3cret@db:5432/x")
    out = FY.run_funnel_sync.run()
    assert out["mautic_rc"] == 0 and out["lake_rc"] == 0 and out["dbt_rc"] == 2
    assert len(out["mautic"]) == 500
    assert calls[0][0] == ["python", "scripts/mautic_sync.py", "--apply"] and calls[0][1]["cwd"] == "/app"
    assert calls[1][0][1] == "scripts/lake_export.py"
    dbt_cmd, dbt_kw = calls[2]
    assert dbt_cmd[:2] == ["dbt", "run"] and dbt_kw["cwd"] == "/app/dbt"
    assert dbt_kw["env"]["PGPASSWORD"] == "s3cret"
    assert dbt_kw["env"]["DBT_TARGET_PATH"] == "/tmp/dbt/target"


def test_funnel_sync_dbt_no_password(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(FY.subprocess, "run", fake_run)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    assert FY._dbt_run() == (0, "")
    assert seen["env"]["PGPASSWORD"] == ""
