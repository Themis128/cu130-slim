"""Coverage for the agency API handlers and the agency/kakkava Celery sweeps.

Handlers are called directly with a scripted fake ``AsyncSession`` so the
tests stay DB-free; the Celery tasks are exercised through their enabled,
disabled and failure branches with ``run_async`` stubbed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.agency as API
import app.core.config as config
import app.worker.tasks.agency as T
import app.worker.tasks.kakkava_sync as K
from app.models.agency import (
    AgencyAuditStatus,
    AgencyProjectStatus,
    AgencyProposal,
    AgencyProposalStatus,
)
from app.services.url_safety import UnsafeUrlError

TEAM = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4(), email="ops@example.com")


class _Scalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def all(self):
        return self._value


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _Scalars(self._value)

    def scalar_one_or_none(self):
        return self._value


class _DB:
    """Returns the queued values from successive ``execute`` calls."""

    def __init__(self, *values):
        self._values = list(values)
        self.added = []
        self.commits = 0

    async def execute(self, _q):
        return _Result(self._values.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None


def _proposal(**kw):
    base = dict(
        id=uuid.uuid4(), team_id=TEAM, status=AgencyProposalStatus.draft,
        meta_data={}, lead_id=None, espo_lead_id="E1",
        recipient_email="client@example.com",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# lookup helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_id_missing_raises_404():
    with pytest.raises(HTTPException) as ei:
        await API._team_id(_DB(None), USER)
    assert ei.value.status_code == 404


@pytest.mark.parametrize(
    "fn", [API._team_audit, API._team_proposal, API._team_project]
)
@pytest.mark.asyncio
async def test_team_row_missing_raises_404(fn):
    with pytest.raises(HTTPException) as ei:
        await fn(_DB(None), TEAM, uuid.uuid4())
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# audits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, AgencyAuditStatus.completed])
async def test_list_audits(status):
    rows = [object()]
    out = await API.list_audits(status=status, limit=10, current_user=USER, db=_DB(TEAM, rows))
    assert out is rows


@pytest.mark.asyncio
async def test_get_audit():
    row = object()
    assert await API.get_audit(uuid.uuid4(), current_user=USER, db=_DB(TEAM, row)) is row


@pytest.mark.asyncio
async def test_run_audit_now_success(monkeypatch):
    async def _ok(url):
        return {
            "score": 80, "findings": [{"k": 1}], "recommended_services": ["seo"],
            "action_plan": "plan", "ttfb_ms": 120, "http_status": 200,
            "tech": [], "analytics": [],
        }

    monkeypatch.setattr(API, "run_site_audit", _ok)
    db = _DB(TEAM)
    audit = await API.run_audit_now(
        API.AuditRunRequest(url="https://example.com"), current_user=USER, db=db
    )
    assert audit.status == AgencyAuditStatus.completed
    assert audit.score == 80 and audit.meta_data["manual"] is True
    assert audit.completed_at is not None and db.commits == 1


@pytest.mark.asyncio
async def test_run_audit_now_unsafe_url(monkeypatch):
    async def _unsafe(url):
        raise UnsafeUrlError("private address")

    monkeypatch.setattr(API, "run_site_audit", _unsafe)
    db = _DB(TEAM)
    with pytest.raises(HTTPException) as ei:
        await API.run_audit_now(API.AuditRunRequest(url="http://10.0.0.1"), current_user=USER, db=db)
    assert ei.value.status_code == 422
    assert db.added[0].status == AgencyAuditStatus.failed
    assert db.added[0].error.startswith("unsafe_url:")


@pytest.mark.asyncio
async def test_run_audit_now_generic_failure(monkeypatch):
    async def _boom(url):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(API, "run_site_audit", _boom)
    db = _DB(TEAM)
    with pytest.raises(HTTPException) as ei:
        await API.run_audit_now(API.AuditRunRequest(url="https://example.com"), current_user=USER, db=db)
    assert ei.value.status_code == 502
    assert db.added[0].error == "unreachable"


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, AgencyProposalStatus.draft])
async def test_list_proposals(status):
    rows = [object()]
    out = await API.list_proposals(status=status, limit=5, current_user=USER, db=_DB(TEAM, rows))
    assert out is rows


@pytest.mark.asyncio
async def test_get_proposal():
    row = _proposal()
    assert await API.get_proposal(row.id, current_user=USER, db=_DB(TEAM, row)) is row


@pytest.mark.asyncio
async def test_approve_proposal_ok(monkeypatch):
    seen = {}

    async def _send(db, proposal, approver):
        seen["approver"] = approver
        return {"ok": True, "sent": True}

    monkeypatch.setattr(API, "send_proposal", _send)
    row = _proposal()
    out = await API.approve_proposal(row.id, current_user=USER, db=_DB(TEAM, row))
    assert out["ok"] is True and seen["approver"] == "ops@example.com"


@pytest.mark.asyncio
async def test_approve_proposal_conflict(monkeypatch):
    async def _send(db, proposal, approver):
        return {"ok": False, "error": "not draft"}

    monkeypatch.setattr(API, "send_proposal", _send)
    row = _proposal()
    with pytest.raises(HTTPException) as ei:
        await API.approve_proposal(row.id, current_user=USER, db=_DB(TEAM, row))
    assert ei.value.status_code == 409 and ei.value.detail == "not draft"


@pytest.mark.asyncio
async def test_reject_proposal_marks_rejected():
    row = _proposal(meta_data=None)
    db = _DB(TEAM, row)
    out = await API.reject_proposal(row.id, current_user=USER, db=db)
    assert out.status == AgencyProposalStatus.rejected
    assert out.meta_data == {"rejected_by": "ops@example.com"} and db.commits == 1


@pytest.mark.asyncio
async def test_reject_proposal_non_draft_conflict():
    row = _proposal(status=AgencyProposalStatus.sent)
    with pytest.raises(HTTPException) as ei:
        await API.reject_proposal(row.id, current_user=USER, db=_DB(TEAM, row))
    assert ei.value.status_code == 409


# ---------------------------------------------------------------------------
# projects + catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, AgencyProjectStatus.active])
async def test_list_projects(status):
    rows = [object()]
    out = await API.list_projects(status=status, limit=5, current_user=USER, db=_DB(TEAM, rows))
    assert out is rows


@pytest.mark.asyncio
async def test_get_project():
    row = object()
    assert await API.get_project(uuid.uuid4(), current_user=USER, db=_DB(TEAM, row)) is row


@pytest.mark.asyncio
async def test_create_project_defaults_kickoff_now():
    db = _DB(TEAM)
    body = API.ProjectCreateRequest(name="Site revamp", client_email="c@example.com")
    project = await API.create_project(body, current_user=USER, db=db)
    assert project.kickoff_at.tzinfo is not None
    assert (project.due_at - project.kickoff_at).days == API.DELIVERY_DAYS - 1
    assert project.client_email == "c@example.com" and project.milestones
    assert db.commits == 1


@pytest.mark.asyncio
async def test_create_project_from_proposal_naive_kickoff():
    prop = _proposal()
    db = _DB(TEAM, prop)
    body = API.ProjectCreateRequest(
        name="Migration", proposal_id=prop.id, kickoff_at=datetime(2026, 1, 1, 9, 0)
    )
    project = await API.create_project(body, current_user=USER, db=db)
    assert project.kickoff_at.tzinfo is UTC
    assert project.client_email == "client@example.com"
    assert project.espo_lead_id == "E1"


@pytest.mark.asyncio
async def test_service_catalog_shape():
    out = await API.service_catalog(current_user=USER)
    assert out["services"] and out["delivery_days"] == API.DELIVERY_DAYS
    assert {"slug", "name", "price_low_eur", "price_high_eur", "summary"} <= set(out["services"][0])


def test_proposal_model_importable():
    # keeps the model import honest for the scripted DB above
    assert AgencyProposal.__tablename__


# ---------------------------------------------------------------------------
# agency Celery sweeps
# ---------------------------------------------------------------------------

SWEEPS = [
    ("run_agency_triage_sweep", "triage_sweep"),
    ("run_agency_audit_sweep", "audit_sweep"),
    ("run_agency_proposal_sweep", "proposal_sweep"),
    ("run_agency_pm_sweep", "pm_sweep"),
    ("run_agency_engineer_sweep", "engineer_sweep"),
]


def _settings(monkeypatch, enabled):
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=enabled))


@pytest.mark.parametrize("task_name,fn_name", SWEEPS)
def test_agency_sweep_disabled(monkeypatch, task_name, fn_name):
    _settings(monkeypatch, False)
    assert getattr(T, task_name)() is None


@pytest.mark.parametrize("task_name,fn_name", SWEEPS)
def test_agency_sweep_runs(monkeypatch, task_name, fn_name):
    _settings(monkeypatch, True)

    async def _sweep():
        return {"swept": fn_name}

    monkeypatch.setattr(T.agency_pipeline, fn_name, _sweep)

    def _run(coro):
        coro.close()
        return {"swept": fn_name}

    monkeypatch.setattr(T, "run_async", _run)
    assert getattr(T, task_name)() == {"swept": fn_name}


@pytest.mark.parametrize("task_name,fn_name", SWEEPS)
def test_agency_sweep_failure_swallowed(monkeypatch, task_name, fn_name):
    _settings(monkeypatch, True)

    async def _sweep():
        return None

    monkeypatch.setattr(T.agency_pipeline, fn_name, _sweep)

    def _boom(coro):
        coro.close()
        raise RuntimeError("db down")

    monkeypatch.setattr(T, "run_async", _boom)
    assert getattr(T, task_name)() is None


# ---------------------------------------------------------------------------
# kakkava corpus sync
# ---------------------------------------------------------------------------


def test_kakkava_task_disabled(monkeypatch):
    monkeypatch.setattr(K.kakkava, "kakkava_enabled", lambda: False)
    assert K.sync_kakkava_corpus() is None


def test_kakkava_task_runs(monkeypatch):
    monkeypatch.setattr(K.kakkava, "kakkava_enabled", lambda: True)

    def _run(coro):
        coro.close()
        return {"t": {"files": 1}}

    monkeypatch.setattr(K, "run_async", _run)
    assert K.sync_kakkava_corpus() == {"t": {"files": 1}}


def test_kakkava_task_failure_reported(monkeypatch):
    monkeypatch.setattr(K.kakkava, "kakkava_enabled", lambda: True)

    def _boom(coro):
        coro.close()
        raise RuntimeError("chroma down")

    monkeypatch.setattr(K, "run_async", _boom)
    assert K.sync_kakkava_corpus() == {"error": "chroma down"}


@pytest.mark.asyncio
async def test_kakkava_sync_all_teams(monkeypatch):
    ok_team = SimpleNamespace(id=uuid.uuid4())
    bad_team = SimpleNamespace(id=uuid.uuid4())
    db = _DB([ok_team, bad_team])

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(K, "get_session_factory", lambda: _Session)

    async def _sync(team_id):
        if team_id == bad_team.id:
            raise RuntimeError("bad file")
        return {"files": 3, "unchanged": 2, "chunks_added": 4, "chunks_removed": 0, "errors": 0}

    monkeypatch.setattr(K.kakkava, "sync_corpus", _sync)
    out = await K._sync_all_teams()
    assert out[str(ok_team.id)]["files"] == 3
    assert out[str(bad_team.id)] == {"error": "bad file"}
    assert len(db.added) == 2 and db.commits == 1
    assert "files=3" in db.added[0].detail and "files=0" in db.added[1].detail
