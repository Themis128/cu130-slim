"""Agency pipeline coverage — API endpoints, worker wrappers, sweep paths.

Fills the CI coverage gate: app/api/agency.py (endpoint handlers), the five
run_agency_*_sweep task wrappers, and the async sweep bodies' empty/happy
paths. Fake-DB pattern matches test_content_api.py.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.agency as A
import app.services.agency_pipeline as P
from app.models.agency import (
    AgencyAuditStatus,
    AgencyProjectStatus,
    AgencyProposalStatus,
)
from app.services.url_safety import UnsafeUrlError

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ScalarRes:
    """Result of .scalars() — first()/all() return the raw column values."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Res:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def scalars(self):
        return _ScalarRes(self._rows)

    def all(self):
        return list(self._rows)

    def first(self):
        # Row-like — code does row[0] on unscalar'd execute().first()
        return (self._rows[0],) if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar

    def one(self):
        return self._scalar


class _DB:
    """Queue-driven fake AsyncSession — each execute() pops one _Res."""

    def __init__(self, *results):
        self._q = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    def add(self, o):
        if getattr(o, "id", None) is None:
            o.id = uuid.uuid4()
        self.added.append(o)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, o, attrs=None):
        if getattr(o, "created_at", None) is None:
            o.created_at = datetime.now(UTC)


class _CM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _factory(db):
    return lambda: _CM(db)


def _team():
    tid = uuid.uuid4()
    return _Res(scalar=tid), tid


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="ops@cloudless.gr", name="ops")


def _audit(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        url="https://acme.example",
        status=AgencyAuditStatus.completed,
        score=42,
        findings=[],
        tech=[],
        recommended_services=[],
        action_plan=None,
        error=None,
        lead_id=None,
        espo_lead_id=None,
        meta_data={},
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _proposal(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        audit_id=None,
        lead_id=None,
        espo_lead_id=None,
        recipient_email="c@acme.example",
        recipient_name="Acme",
        services=["cloud"],
        price_low_eur=100,
        price_high_eur=200,
        subject="s",
        body_md="b",
        meta_data={},
        status=AgencyProposalStatus.draft,
        sent_at=None,
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _project(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        proposal_id=None,
        lead_id=None,
        espo_lead_id=None,
        name="Acme job",
        client_email="c@acme.example",
        kickoff_at=datetime.now(UTC),
        due_at=datetime.now(UTC),
        status=AgencyProjectStatus.active,
        milestones=[],
        handoff_md=None,
        last_client_update_at=None,
        meta_data={},
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


# ---------------------------------------------------------------------------
# app/api/agency.py — helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_id_found_and_404():
    tid = uuid.uuid4()
    db = _DB(_Res(rows=[tid]))
    assert await A._team_id(db, _user()) == tid
    with pytest.raises(HTTPException) as ei:
        await A._team_id(_DB(_Res()), _user())
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_team_row_helpers_found_and_404():
    for helper, obj in (
        (A._team_audit, _audit()),
        (A._team_proposal, _proposal()),
        (A._team_project, _project()),
    ):
        db = _DB(_Res(scalar=obj))
        assert await helper(db, uuid.uuid4(), obj.id) is obj
        with pytest.raises(HTTPException) as ei:
            await helper(_DB(_Res(scalar=None)), uuid.uuid4(), uuid.uuid4())
        assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# app/api/agency.py — endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_and_get_endpoints():
    tid = uuid.uuid4()
    audit, prop, proj = _audit(), _proposal(), _project()

    db = _DB(_Res(rows=[tid]), _Res(rows=[audit]))
    out = await A.list_audits(status=AgencyAuditStatus.completed, limit=10, current_user=_user(), db=db)
    assert out == [audit]

    db = _DB(_Res(rows=[tid]), _Res(rows=[audit]))
    out = await A.list_audits(status=None, limit=10, current_user=_user(), db=db)
    assert out == [audit]

    db = _DB(_Res(rows=[tid]), _Res(scalar=audit))
    assert await A.get_audit(audit.id, current_user=_user(), db=db) is audit

    db = _DB(_Res(rows=[tid]), _Res(rows=[prop]))
    out = await A.list_proposals(status=AgencyProposalStatus.draft, limit=10, current_user=_user(), db=db)
    assert out == [prop]

    db = _DB(_Res(rows=[tid]), _Res(scalar=prop))
    assert await A.get_proposal(prop.id, current_user=_user(), db=db) is prop

    db = _DB(_Res(rows=[tid]), _Res(rows=[proj]))
    out = await A.list_projects(status=AgencyProjectStatus.active, limit=10, current_user=_user(), db=db)
    assert out == [proj]

    db = _DB(_Res(rows=[tid]), _Res(scalar=proj))
    assert await A.get_project(proj.id, current_user=_user(), db=db) is proj


@pytest.mark.asyncio
async def test_service_catalog():
    out = await A.service_catalog(current_user=_user())
    assert len(out["services"]) == 5 and out["delivery_days"] == 14
    lg = next(s for s in out["services"] if s["slug"] == "linkedin_growth")
    assert lg["name"] == "LinkedIn B2B Growth"
    assert out["bundle_example_price_range"] == (3600, 3600)


@pytest.mark.asyncio
async def test_run_audit_now_success(monkeypatch):
    tid = uuid.uuid4()
    result = {
        "url": "https://acme.example",
        "score": 88,
        "findings": [],
        "recommended_services": ["cloud"],
        "action_plan": "plan",
        "ttfb_ms": 120,
        "http_status": 200,
        "tech": ["nginx"],
        "analytics": ["ga4"],
    }
    monkeypatch.setattr(A, "run_site_audit", AsyncMock(return_value=result))
    db = _DB(_Res(rows=[tid]))
    out = await A.run_audit_now(A.AuditRunRequest(url="https://acme.example"), current_user=_user(), db=db)
    assert out.status == AgencyAuditStatus.completed and out.score == 88
    assert out.meta_data["manual"] is True and db.commits == 1


@pytest.mark.asyncio
async def test_run_audit_now_unsafe_and_error(monkeypatch):
    tid = uuid.uuid4()
    monkeypatch.setattr(A, "run_site_audit", AsyncMock(side_effect=UnsafeUrlError("private ip")))
    db = _DB(_Res(rows=[tid]))
    with pytest.raises(HTTPException) as ei:
        await A.run_audit_now(A.AuditRunRequest(url="http://10.0.0.1"), current_user=_user(), db=db)
    assert ei.value.status_code == 422 and "private ip" in ei.value.detail
    assert db.added[0].status == AgencyAuditStatus.failed

    monkeypatch.setattr(A, "run_site_audit", AsyncMock(side_effect=RuntimeError("down")))
    db = _DB(_Res(rows=[tid]))
    with pytest.raises(HTTPException) as ei:
        await A.run_audit_now(A.AuditRunRequest(url="https://x.example"), current_user=_user(), db=db)
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_approve_proposal_ok_and_409(monkeypatch):
    tid = uuid.uuid4()
    prop = _proposal()
    monkeypatch.setattr(A, "send_proposal", AsyncMock(return_value={"ok": True, "sent": True}))
    db = _DB(_Res(rows=[tid]), _Res(scalar=prop))
    out = await A.approve_proposal(prop.id, current_user=_user(), db=db)
    assert out["ok"] is True

    monkeypatch.setattr(A, "send_proposal", AsyncMock(return_value={"ok": False, "error": "not draft"}))
    db = _DB(_Res(rows=[tid]), _Res(scalar=prop))
    with pytest.raises(HTTPException) as ei:
        await A.approve_proposal(prop.id, current_user=_user(), db=db)
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_reject_proposal_ok_and_409():
    tid = uuid.uuid4()
    prop = _proposal()
    db = _DB(_Res(rows=[tid]), _Res(scalar=prop))
    out = await A.reject_proposal(prop.id, current_user=_user(), db=db)
    assert out.status == AgencyProposalStatus.rejected
    assert out.meta_data["rejected_by"] == "ops@cloudless.gr"

    sent = _proposal(status=AgencyProposalStatus.sent)
    db = _DB(_Res(rows=[tid]), _Res(scalar=sent))
    with pytest.raises(HTTPException) as ei:
        await A.reject_proposal(sent.id, current_user=_user(), db=db)
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_create_project_with_and_without_proposal():
    tid = uuid.uuid4()
    # plain project — naive kickoff gets tzinfo, due = kickoff + 13d
    db = _DB(_Res(rows=[tid]))
    out = await A.create_project(A.ProjectCreateRequest(name="Acme", client_email="c@x.io", kickoff_at=datetime(2026, 11, 1)), current_user=_user(), db=db)
    assert out.kickoff_at.tzinfo is not None
    assert (out.due_at - out.kickoff_at).days == 13
    assert out.lead_id is None and out.client_email == "c@x.io"
    assert len(out.milestones) == 4

    # from a sent proposal — inherits lead + recipient email
    prop = _proposal(lead_id=uuid.uuid4(), espo_lead_id="e1", recipient_email="won@acme.example")
    db = _DB(_Res(rows=[tid]), _Res(scalar=prop))
    out = await A.create_project(
        A.ProjectCreateRequest(name="From proposal", proposal_id=prop.id, client_email=None, kickoff_at=None), current_user=_user(), db=db
    )
    assert out.lead_id == prop.lead_id and out.espo_lead_id == "e1"
    assert out.client_email == "won@acme.example"
    assert out.kickoff_at.tzinfo is not None


# ---------------------------------------------------------------------------
# app/worker/tasks/agency.py — wrappers
# ---------------------------------------------------------------------------

_TASKS = [
    ("run_agency_triage_sweep", "triage_sweep"),
    ("run_agency_audit_sweep", "audit_sweep"),
    ("run_agency_proposal_sweep", "proposal_sweep"),
    ("run_agency_pm_sweep", "pm_sweep"),
    ("run_agency_engineer_sweep", "engineer_sweep"),
]


@pytest.mark.parametrize("task_name,sweep_name", _TASKS)
def test_task_disabled_returns_none(monkeypatch, task_name, sweep_name):
    import app.worker.tasks.agency as T

    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=False))
    assert getattr(T, task_name)() is None


@pytest.mark.parametrize("task_name,sweep_name", _TASKS)
def test_task_enabled_runs_sweep(monkeypatch, task_name, sweep_name):
    import app.worker.tasks.agency as T

    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=True))

    async def _s():
        return {"ok": sweep_name}

    monkeypatch.setattr(T.agency_pipeline, sweep_name, _s)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert getattr(T, task_name)() == {"ok": sweep_name}


@pytest.mark.parametrize("task_name,sweep_name", _TASKS)
def test_task_failure_swallowed(monkeypatch, task_name, sweep_name):
    import app.worker.tasks.agency as T

    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(AGENCY_ENABLED=True))

    async def _boom():
        raise RuntimeError("sweep down")

    monkeypatch.setattr(T.agency_pipeline, sweep_name, _boom)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert getattr(T, task_name)() is None


# ---------------------------------------------------------------------------
# agency_pipeline sweeps — disabled gates + empty/happy paths
# ---------------------------------------------------------------------------


def _settings(**kw):
    base = dict(AGENCY_ENABLED=True, AGENCY_AUDIT_MAX_PER_RUN=5)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_sweeps_disabled(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings(AGENCY_ENABLED=False))
    assert (await P.triage_sweep())["status"] == "disabled"
    assert (await P.audit_sweep())["status"] == "disabled"
    assert (await P.proposal_sweep())["status"] == "disabled"
    assert (await P.pm_sweep())["status"] == "disabled"
    assert (await P.engineer_sweep())["status"] == "disabled"


@pytest.mark.asyncio
async def test_triage_sweep_no_team_and_empty(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    # no team → skipped
    r = await P.triage_sweep(session_factory=_factory(_DB(_Res())))
    assert r["status"] == "skipped" and r["reason"] == "no_team"
    # team, no leads, espo not configured → all zeros
    monkeypatch.setattr(P.espo, "configured", lambda: False)
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res())
    r = await P.triage_sweep(session_factory=_factory(db))
    assert r["status"] == "ok"
    assert r["local"]["triaged"] == 0 and r["espo"]["triaged"] == 0


@pytest.mark.asyncio
async def test_audit_sweep_empty_and_paths(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: False)
    r = await P.audit_sweep(session_factory=_factory(_DB(_Res())))
    assert r == {"status": "ok", "completed": 0, "failed": 0}

    # one ok + one raise + one unsafe
    good, boom, unsafe = (_audit(status=AgencyAuditStatus.pending, url=f"https://{n}.example") for n in ("good", "boom", "unsafe"))
    seen_notes = []

    async def _note(lid, text):
        seen_notes.append(text)
        return True

    monkeypatch.setattr(P.espo, "add_lead_note", _note)
    good.espo_lead_id = "e9"
    result = {
        "url": "https://good.example",
        "score": 80,
        "findings": [{"finding": "f1", "severity": "warning"}],
        "recommended_services": [],
        "action_plan": "p",
        "ttfb_ms": 10,
        "http_status": 200,
        "tech": ["nginx"],
        "analytics": [],
    }
    calls = []

    async def _fake_site_audit(url):
        calls.append(url)
        if "boom" in url:
            raise RuntimeError("down")
        if "unsafe" in url:
            raise UnsafeUrlError("no")
        return dict(result)

    monkeypatch.setattr(P, "run_site_audit", _fake_site_audit)
    db = _DB(_Res(rows=[good, boom, unsafe]))
    r = await P.audit_sweep(session_factory=_factory(db))
    assert r["completed"] == 1 and r["failed"] == 2
    assert good.status == AgencyAuditStatus.completed
    assert boom.status == AgencyAuditStatus.failed
    assert unsafe.status == AgencyAuditStatus.failed
    assert "unsafe_url" in unsafe.error
    assert seen_notes and "80/100" in seen_notes[0]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_proposal_sweep_empty_and_drafts(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: False)
    r = await P.proposal_sweep(session_factory=_factory(_DB(_Res())))
    assert r == {"status": "ok", "drafted": 0}

    # one completed audit, no existing proposal → draft + slack (swallowed)
    audit = _audit(
        status=AgencyAuditStatus.completed,
        score=70,
        recommended_services=["cloud"],
        findings=[{"finding": "No HSTS", "severity": "warning", "service": "cloud"}],
        action_plan="p",
        meta_data={"lead_email": "c@acme.example", "lead_name": "Acme"},
    )
    db = _DB(_Res(rows=[audit]), _Res(scalar=None), _Res(scalar=None))
    r = await P.proposal_sweep(session_factory=_factory(db))
    assert r["drafted"] == 1
    assert db.added and db.added[0].status == AgencyProposalStatus.draft
    assert db.added[0].recipient_email == "c@acme.example"


@pytest.mark.asyncio
async def test_engineer_sweep_empty_and_plans(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    r = await P.engineer_sweep(session_factory=_factory(_DB(_Res())))
    assert r == {"status": "ok", "planned": 0}

    sent = _proposal(status=AgencyProposalStatus.sent, meta_data={})
    db = _DB(_Res(rows=[sent]))
    r = await P.engineer_sweep(session_factory=_factory(db))
    assert r["planned"] == 1
    assert sent.meta_data["engineering_plan"]["plan_md"]

    # already planned → skipped
    sent2 = _proposal(status=AgencyProposalStatus.sent, meta_data={"engineering_plan": {"plan_md": "x"}})
    db = _DB(_Res(rows=[sent2]))
    r = await P.engineer_sweep(session_factory=_factory(db))
    assert r["planned"] == 0


@pytest.mark.asyncio
async def test_pm_sweep_empty_update_deliver_overdue(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    r = await P.pm_sweep(session_factory=_factory(_DB(_Res())))
    assert r["status"] == "ok" and not any(r[k] for k in ("updates", "delivered", "overdue"))

    # project at day 5 → update drafted
    kickoff = datetime.now(UTC) - timedelta(days=4)
    p1 = _project(kickoff_at=kickoff, due_at=kickoff + timedelta(days=13), milestones=P.project_milestones())
    db = _DB(_Res(rows=[p1]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert r["updates"] == 2 and p1.last_client_update_at is not None
    assert p1.milestones[0]["done"] is True and p1.milestones[1]["done"] is True

    # fully-done project at day 14+ → delivered + handoff
    kickoff = datetime.now(UTC) - timedelta(days=15)
    ms = P.project_milestones()
    for m in ms:
        m["done"] = True
        m["done_at"] = datetime.now(UTC).isoformat()
    p2 = _project(kickoff_at=kickoff, due_at=kickoff + timedelta(days=13), milestones=ms)
    db = _DB(_Res(rows=[p2]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert r["delivered"] == 1 and p2.status == AgencyProjectStatus.delivered
    assert p2.handoff_md

    # past a custom early due_at (+2d grace) while <day 14 → stalled, not
    # delivered (handoff path needs day >= DELIVERY_DAYS)
    kickoff = datetime.now(UTC) - timedelta(days=10)
    p3 = _project(kickoff_at=kickoff, due_at=kickoff + timedelta(days=6), milestones=P.project_milestones())
    db = _DB(_Res(rows=[p3]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert r["overdue"] == 1 and p3.status == AgencyProjectStatus.stalled


# ---------------------------------------------------------------------------
# send_proposal + _triage_local_leads paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_proposal_not_draft_and_no_recipient():
    prop = _proposal(status=AgencyProposalStatus.sent)
    out = await P.send_proposal(_DB(), prop, approver="x")
    assert out["ok"] is False and "draft" in out["error"]
    out = await P.send_proposal(_DB(), _proposal(recipient_email=None), approver="x")
    assert out["ok"] is False and "recipient" in out["error"].lower()


@pytest.mark.asyncio
async def test_triage_local_leads_classifies_and_dedupes(monkeypatch):
    # one untriaged lead with a site URL → triage note + audit row; a second
    # same-domain lead dedupes via the audit-exists check.
    monkeypatch.setattr(P.espo, "configured", lambda: False)
    notes = []

    async def _note(lid, text):
        notes.append(text)
        return True

    monkeypatch.setattr(P.espo, "add_lead_note", _note)

    from app.models.lead import Lead

    lead1 = Lead(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="A",
        email="",
        notes="we need a cloud migration for acme.io site is acme.io",
        meta_data={"espocrm_lead_id": "esp1"},
    )
    lead1.id = uuid.uuid4()
    lead2 = Lead(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="B",
        email="",
        notes="cloud migration please, domain acme.io",
        meta_data={},
    )
    lead2.id = uuid.uuid4()
    db = _DB(
        _Res(rows=[lead1, lead2]),  # local untriaged leads select
        _Res(),  # dup-check for lead1's domain → none
        _Res(rows=[1]),  # dup-check for lead2's domain → exists
    )
    stats = await P._triage_local_leads(db, lead1.team_id)
    assert stats["triaged"] == 2 and stats["audits"] == 1
    assert db.added and db.added[0].url == "https://acme.io"
    assert len(notes) == 1 and "agency" in notes[0].lower()


@pytest.mark.asyncio
async def test_triage_espo_leads_path(monkeypatch):
    monkeypatch.setattr(P.espo, "configured", lambda: True)
    notes, triage = [], []
    lead = {"id": "e1", "name": "EspoCo", "emailAddress": "e@espo.io", "description": "we want seo marketing for our shop espo-shop.gr"}

    async def _list(entity, where=None, **kw):
        if entity == "Note":
            return triage if where is None else []
        return [lead]

    async def _note(lid, text):
        notes.append(text)
        return True

    monkeypatch.setattr(P.espo, "list_all", _list)
    monkeypatch.setattr(P.espo, "add_lead_note", _note)
    db = _DB(_Res(), _Res())  # agency_audit-by-espo-id check; domain dup-check
    stats = await P._triage_espo_leads(db, uuid.uuid4())
    assert stats["triaged"] == 1 and stats["audits"] == 1
    assert db.added and db.added[0].espo_lead_id == "e1"
    assert "marketing" in db.added[0].url or "espo-shop" in db.added[0].url
    assert notes and "Marketing" in notes[0] or "marketing" in notes[0]


@pytest.mark.asyncio
async def test_public_audit_endpoint(monkeypatch):
    """POST /leads/public/audit runs a real audit, creates lead+audit rows."""
    from fastapi import HTTPException

    import app.api.leads as L

    audit_result = {
        "url": "https://acme.test/",
        "score": 42,
        "findings": [{"detail": "missing HSTS"}],
        "recommended_services": ["cloud"],
        "action_plan": "fix headers",
    }
    monkeypatch.setattr("app.services.site_audit.run_site_audit", lambda u: asyncio.sleep(0, audit_result))
    monkeypatch.setattr("app.services.url_safety.validate_public_http_url", lambda u: u)
    monkeypatch.setattr("app.api.leads.create_lead", lambda *a, **k: asyncio.sleep(0, SimpleNamespace(id=uuid.uuid4())))

    class _S:
        CLOUDLESS_WEB_ANALYTICS_TEAM_ID = str(uuid.uuid4())

    monkeypatch.setattr("app.api.leads.get_settings", lambda: _S())
    L.limiter.enabled = False  # slowapi hits real Redis otherwise

    added = []

    class _DB:
        async def flush(self):
            pass

        def add(self, r):
            added.append(r)

        async def commit(self):
            pass

    from starlette.requests import Request

    fake_req = Request({"type": "http", "method": "POST", "path": "/x", "headers": [], "client": ("127.0.0.1", 1), "server": ("t", 80)})
    req = L.PublicAuditRequest(email="lead@acme.test", url="https://acme.test")
    out = await L.create_audit_public(req, fake_req, _DB())
    assert out["ok"] and out["score"] == 42 and out["issues"] == 1
    assert added and added[0].url == "https://acme.test/" and added[0].score == 42

    # unsafe URL → 400
    from app.services.url_safety import UnsafeUrlError

    monkeypatch.setattr("app.services.url_safety.validate_public_http_url", lambda u: (_ for _ in ()).throw(UnsafeUrlError("bad")))
    try:
        await L.create_audit_public(req, fake_req, _DB())
        raise AssertionError("should 400")
    except HTTPException as e:
        assert e.status_code == 400

    # honeypot → silent ok
    req2 = L.PublicAuditRequest(email="lead@acme.test", url="https://acme.test", website="spam")
    out2 = await L.create_audit_public(req2, request=fake_req, db=_DB())
    assert out2["ok"] and out2["score"] is None


@pytest.mark.asyncio
async def test_public_survey_endpoint(monkeypatch):
    """POST /leads/public/survey — richer capture: phone + answers → lead+espo."""
    import app.api.leads as L

    monkeypatch.setattr("app.api.leads.create_lead", lambda *a, **k: asyncio.sleep(0, None))
    captured = []

    async def fake_upsert(payload):
        captured.append(payload)

    monkeypatch.setattr("app.services.espocrm_client.upsert_lead", fake_upsert)
    L.limiter.enabled = False

    class _S:
        CLOUDLESS_WEB_ANALYTICS_TEAM_ID = str(uuid.uuid4())
        PLAYBOOK_PDF_URL = "https://x/playbook.pdf"

    monkeypatch.setattr("app.api.leads.get_settings", lambda: _S())

    class _DB:
        async def commit(self):
            pass

        async def flush(self):
            pass

    from starlette.requests import Request

    fake_req = Request({"type": "http", "method": "POST", "path": "/x", "headers": [], "client": ("127.0.0.1", 1), "server": ("t", 80)})

    req = L.PublicSurveyRequest(
        email="lead@acme.test", name="Jane D", phone="+30 691 234 5678", company_size="11-50", interest="cloud migration", answers={"pain": "manual deploys"}
    )
    out = await L.create_survey_public(req, fake_req, _DB())
    assert out["ok"] and captured
    assert captured[0]["phoneNumber"] == "+30 691 234 5678"
    assert "manual deploys" in captured[0]["description"]

    # bad phone → 400
    req2 = L.PublicSurveyRequest(email="a@b.co", phone="123")
    try:
        await L.create_survey_public(req2, fake_req, _DB())
        raise AssertionError("should 400")
    except HTTPException as e:
        assert e.status_code == 400

    # phone without consent → 400
    req3 = L.PublicSurveyRequest(email="a@b.co", phone="+30 691 234 5678", consent=False)
    try:
        await L.create_survey_public(req3, fake_req, _DB())
        raise AssertionError("should 400")
    except HTTPException as e:
        assert e.status_code == 400
