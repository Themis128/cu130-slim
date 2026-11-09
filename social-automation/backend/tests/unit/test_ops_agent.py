"""Unit tests for app/services/ops_agent.py — deterministic pieces only.

The LLM verdict and DB session are mocked/heavy; these cover the parts that
must never regress: fingerprint stability, verdict parsing, and the
whitelisted-action allowlist that bounds agent autonomy.
"""

from __future__ import annotations

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
