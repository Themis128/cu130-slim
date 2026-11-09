"""Unit tests for app/services/ops_agent.py — deterministic pieces only.

The LLM verdict and DB session are mocked/heavy; these cover the parts that
must never regress: fingerprint stability, verdict parsing, and the
whitelisted-action allowlist that bounds agent autonomy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.ops_incident import (
    IncidentSeverity,
    IncidentStatus,
    OpsIncident,
)
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


# ---------------------------------------------------------------- async paths


def _inc(**kw) -> OpsIncident:
    base = dict(
        id=uuid4(),
        fingerprint="f" * 32,
        source="test",
        kind="k",
        target="",
        severity=IncidentSeverity.OPS,
        title="t",
        detail={},
        status=IncidentStatus.OPEN,
        rca="",
        proposed_action="",
        action_taken="",
        action_result="",
        agent_attempts=0,
        recurrence_count=0,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(kw)
    return OpsIncident(**base)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one(self):
        return self._scalar


class _Session:
    """AsyncSession stand-in: execute() pops queued results in order."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0
        self.flushes = 0

    async def execute(self, stmt):
        return self._results.pop(0) if self._results else _Result()

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_report_incident_upserts_and_returns_row():
    inc = _inc()
    db = _Session([_Result(), _Result(scalar=inc)])
    out = await oa.report_incident(
        db, source="s", kind="k", title="hello", target="t", severity="critical"
    )
    assert out is inc
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_report_incident_bad_severity_falls_back_to_ops():
    inc = _inc()
    db = _Session([_Result(), _Result(scalar=inc)])
    with patch.object(oa, "fingerprint", return_value="x" * 32):
        await oa.report_incident(db, source="s", kind="k", title="t", severity="bogus")
    # reached execute without raising -> severity coercion path exercised
    assert db.flushes == 1


def test_build_prompt_includes_evidence_and_allowlist():
    inc = _inc(target="social-api", detail={"reason": "down"}, recurrence_count=2)
    prompt = oa._build_prompt(inc, {"social-api": {"status": "Exited", "state": "exited"}})
    assert "social-api" in prompt
    assert "Exited" in prompt
    assert "dmr_health" in prompt
    assert '"action"' in prompt


def test_build_prompt_unknown_container():
    inc = _inc(target="ghost")
    prompt = oa._build_prompt(inc, {})
    assert "unknown" in prompt


@pytest.mark.asyncio
async def test_container_states_parses_docker_proxy():
    payload = [
        {"Names": ["/social-api"], "Status": "Up 2 hours", "State": "running"},
        {"Names": ["/a", "/b"], "Status": "Exited", "State": "exited"},
    ]

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    with patch("httpx.AsyncClient", _Client):
        states = await oa._container_states()
    assert states["social-api"]["state"] == "running"
    assert states["a"]["status"] == "Exited"
    assert states["b"]["state"] == "exited"


@pytest.mark.asyncio
async def test_container_states_proxy_down_returns_empty():
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("conn refused")

    with patch("httpx.AsyncClient", _Client):
        assert await oa._container_states() == {}


@pytest.mark.asyncio
async def test_llm_verdict_parses_inference_response():
    fake_result = {"response": 'noise {"severity":"ops","action":"none","summary":"ok"} tail'}
    with (
        patch("app.services.inference.call_inference", AsyncMock(return_value=fake_result)),
        patch("app.core.config.get_settings") as gs,
    ):
        gs.return_value.DMR_MID_MODEL = "m"
        v = await oa._llm_verdict(_inc(), {}, _Session([]))
    assert v["action"] == "none"


@pytest.mark.asyncio
async def test_dispatch_rerun_known_and_unknown():
    sent = []

    class _Cap:
        def send_task(self, path):
            sent.append(path)

    import celery

    with patch.object(celery, "current_app", _Cap()):
        ok = await oa._dispatch_rerun("dmr_health")
        bad = await oa._dispatch_rerun("nope")
    assert "dispatched" in ok
    assert sent == ["app.worker.tasks.dmr_health.check_dmr_health"]
    assert "unknown" in bad


@pytest.mark.asyncio
async def test_sweep_empty_table_commits_and_returns():
    db = _Session([_Result(rows=[])])  # stale select -> none
    stats = await oa.sweep(db)
    # no open incidents -> only the stale select ran
    assert stats["triaged"] == 0
    assert db.commits == 1


@pytest.mark.asyncio
async def test_sweep_triages_open_incident(monkeypatch):
    inc = _inc()
    db = _Session([
        _Result(rows=[]),               # stale
        _Result(rows=[inc]),            # open
    ])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        oa, "_llm_verdict",
        AsyncMock(return_value={"severity": "ops", "action": "none", "summary": "watch"}),
    )
    alerted = []

    async def _fake_alert(i, sev):
        alerted.append(sev)

    monkeypatch.setattr(oa, "_alert", _fake_alert)
    stats = await oa.sweep(db)
    assert stats["triaged"] == 1
    assert inc.status == IncidentStatus.TRIAGED
    assert inc.rca == "watch"
    assert alerted == ["ops"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_sweep_rerun_action_dispatches(monkeypatch):
    inc = _inc(agent_attempts=0)
    db = _Session([_Result(rows=[]), _Result(rows=[inc])])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        oa, "_llm_verdict",
        AsyncMock(return_value={"action": "rerun:dmr_health", "summary": "stale"}),
    )
    dispatched = []

    async def _fake_dispatch(key):
        dispatched.append(key)
        return "ok"

    monkeypatch.setattr(oa, "_dispatch_rerun", _fake_dispatch)
    monkeypatch.setattr(oa, "_alert", AsyncMock())
    stats = await oa.sweep(db)
    assert stats["actioned"] == 1
    assert dispatched == ["dmr_health"]
    assert inc.status == IncidentStatus.ACTIONED


@pytest.mark.asyncio
async def test_sweep_escalates_after_max_attempts(monkeypatch):
    inc = _inc(agent_attempts=oa.MAX_AGENT_ATTEMPTS)
    db = _Session([_Result(rows=[]), _Result(rows=[inc])])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        oa, "_llm_verdict",
        AsyncMock(return_value={"action": "none", "summary": "still bad"}),
    )
    monkeypatch.setattr(oa, "_alert", AsyncMock())
    stats = await oa.sweep(db)
    assert stats["escalated"] == 1
    assert inc.status == IncidentStatus.ESCALATED


@pytest.mark.asyncio
async def test_sweep_auto_resolves_stale(monkeypatch):
    old = _inc(last_seen=datetime.now(UTC) - timedelta(hours=3))
    db = _Session([_Result(rows=[old]), _Result(rows=[])])
    stats = await oa.sweep(db)
    assert stats["resolved_stale"] == 1
    assert old.status == IncidentStatus.RESOLVED
    assert old.resolved_at is not None


@pytest.mark.asyncio
async def test_sweep_llm_failure_still_triages(monkeypatch):
    inc = _inc()
    db = _Session([_Result(rows=[]), _Result(rows=[inc])])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(oa, "_llm_verdict", AsyncMock(side_effect=RuntimeError("llm down")))
    monkeypatch.setattr(oa, "_alert", AsyncMock())
    stats = await oa.sweep(db)
    assert stats["triaged"] == 1
    assert inc.status == IncidentStatus.TRIAGED


@pytest.mark.asyncio
async def test_sweep_resolve_action(monkeypatch):
    inc = _inc()
    db = _Session([_Result(rows=[]), _Result(rows=[inc])])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        oa, "_llm_verdict",
        AsyncMock(return_value={"action": "resolve", "summary": "gone"}),
    )
    monkeypatch.setattr(oa, "_alert", AsyncMock())
    stats = await oa.sweep(db)
    assert stats["resolved_stale"] == 1
    assert inc.status == IncidentStatus.RESOLVED
    assert inc.resolved_at is not None


@pytest.mark.asyncio
async def test_sweep_escalate_action(monkeypatch):
    inc = _inc()
    db = _Session([_Result(rows=[]), _Result(rows=[inc])])
    monkeypatch.setattr(oa, "_container_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        oa, "_llm_verdict",
        AsyncMock(return_value={"action": "escalate", "summary": "needs human"}),
    )
    alerts = []

    async def _fake_alert(i, sev):
        alerts.append(sev)

    monkeypatch.setattr(oa, "_alert", _fake_alert)
    stats = await oa.sweep(db)
    assert stats["escalated"] == 1
    assert inc.status == IncidentStatus.ESCALATED
    assert alerts == ["ops"]


@pytest.mark.asyncio
async def test_llm_verdict_list_text_and_empty_result():
    with (
        patch(
            "app.services.inference.call_inference",
            AsyncMock(return_value={"response": ["{\"action\":\"none\"}", "noise"]}),
        ),
        patch("app.core.config.get_settings") as gs,
    ):
        gs.return_value.DMR_MID_MODEL = "m"
        v = await oa._llm_verdict(_inc(), {}, _Session([]))
        assert v["action"] == "none"
    # empty result path
    with (
        patch("app.services.inference.call_inference", AsyncMock(return_value={})),
        patch("app.core.config.get_settings") as gs,
    ):
        gs.return_value.DMR_MID_MODEL = "m"
        v = await oa._llm_verdict(_inc(), {}, _Session([]))
        assert v == {}


@pytest.mark.asyncio
async def test_dispatch_rerun_exception_returns_failure():
    class _Cap:
        def send_task(self, path):
            raise RuntimeError("broker down")

    import celery

    with patch.object(celery, "current_app", _Cap()):
        out = await oa._dispatch_rerun("dmr_health")
    assert "dispatch failed" in out


@pytest.mark.asyncio
async def test_alert_sends_telegram_and_swallows_errors(monkeypatch):
    sent = []

    async def _fake_send(sev, text):
        sent.append((sev, text))

    import app.services.slack_notifications as sn

    monkeypatch.setattr(sn, "send_telegram_alert", _fake_send)
    inc = _inc(rca="root cause", proposed_action="", action_taken="", action_result="done")
    await oa._alert(inc, "ops")
    assert sent and sent[0][0] == "ops"
    assert "root cause" in sent[0][1]

    async def _boom(sev, text):
        raise RuntimeError("tg down")

    monkeypatch.setattr(sn, "send_telegram_alert", _boom)
    await oa._alert(inc, "ops")  # must not raise
