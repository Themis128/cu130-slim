"""Funnel-closure sweeps — won_sweep, post_consult_sweep, proposal_followup,
post-delivery testimonial ask, and the crm_engagement proposal-reply stamp.

Same fake-DB queue pattern as test_agency_coverage.py.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.services.agency_pipeline as P
import app.services.crm_engagement as E
import app.services.proposal_followup as F
from app.models.agency import AgencyProjectStatus, AgencyProposalStatus


class _ScalarRes:
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
        return (self._rows[0],) if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
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


class _CM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _factory(db):
    return lambda: _CM(db)


def _settings(**kw):
    base = dict(AGENCY_ENABLED=True, AGENCY_AUDIT_MAX_PER_RUN=5, AGENCY_STRIPE_PROJECT_MIN_EUR=500)
    base.update(kw)
    return SimpleNamespace(**base)


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
        subject="Audit + plan",
        body_md="b",
        meta_data={},
        status=AgencyProposalStatus.sent,
        sent_at=datetime.now(UTC) - timedelta(days=4),
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


def _opp(**kw):
    d = dict(
        id="opp1",
        name="Acme — Cloud migration",
        stage="closedwon",
        amount=1200,
        amountCurrency="EUR",
        closeDate="2026-11-01",
        createdAt="2026-10-28T10:00:00Z",
        accountId=None,
        accountName=None,
        leadSource="calendar_booking",
        description=None,
    )
    d.update(kw)
    return d


def _mail_capture(monkeypatch):
    sent = []

    async def fake_send_email(**kw):
        sent.append(kw)

    monkeypatch.setattr("app.services.email_digest.send_email", fake_send_email)
    return sent


# ── won_sweep ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_won_sweep_disabled_and_unconfigured(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings(AGENCY_ENABLED=False))
    assert (await P.won_sweep())["status"] == "disabled"
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: False)
    assert (await P.won_sweep())["reason"] == "espocrm_not_configured"


@pytest.mark.asyncio
async def test_won_sweep_creates_project_and_kickoff(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: True)
    sent = _mail_capture(monkeypatch)
    notes = []

    async def _note(oid, text, *, entity="Lead"):
        notes.append((oid, text, entity))
        return True

    async def _list_all(entity, **kw):
        if entity == "Opportunity":
            return [_opp()]
        return []  # Note dedupe reads → no marker

    async def _espo_get(path, params=None):
        assert path == "/Opportunity/opp1/contacts"
        return {"list": [{"id": "c1", "name": "Acme", "emailAddress": "buyer@acme.example"}]}

    monkeypatch.setattr(P.espo, "list_all", _list_all)
    monkeypatch.setattr(P.espo, "espo_get", _espo_get)
    monkeypatch.setattr(P.espo, "add_lead_note", _note)

    # team + no existing project + no sent proposal for the buyer
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res(), _Res(scalar=None))
    r = await P.won_sweep(session_factory=_factory(db))
    assert r["created"] == 1
    proj = db.added[0]
    assert proj.status == AgencyProjectStatus.active
    assert proj.client_email == "buyer@acme.example"
    assert proj.meta_data["espo_opportunity_id"] == "opp1"
    assert sent and sent[0]["to_addrs"] == ["buyer@acme.example"]
    assert notes and notes[0][2] == "Opportunity" and P.KICKOFF_MARKER in notes[0][1]


@pytest.mark.asyncio
async def test_won_sweep_skips_low_value_stripe(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: True)

    async def _list_all(entity, **kw):
        return [_opp(leadSource="stripe_checkout", amount=9)] if entity == "Opportunity" else []

    monkeypatch.setattr(P.espo, "list_all", _list_all)
    db = _DB(_Res(rows=[uuid.uuid4()]))
    r = await P.won_sweep(session_factory=_factory(db))
    assert r["created"] == 0 and r["skipped"] == 1
    assert not db.added


@pytest.mark.asyncio
async def test_won_sweep_dedupes_existing_project_and_marker(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: True)

    async def _list_all(entity, **kw):
        if entity == "Opportunity":
            return [_opp()]
        return [{"id": "n1", "post": f"{P.KICKOFF_MARKER} already"}]

    monkeypatch.setattr(P.espo, "list_all", _list_all)
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res())  # no existing project row
    r = await P.won_sweep(session_factory=_factory(db))
    assert r["skipped"] == 1 and not db.added


# ── post_consult_sweep ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_consult_sends_followup(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: True)
    sent = _mail_capture(monkeypatch)
    notes = []

    async def _note(oid, text, *, entity="Lead"):
        notes.append((oid, text, entity))
        return True

    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()

    async def _list_all(entity, **kw):
        if entity == "Opportunity":
            return [_opp(stage=P.CONSULT_STAGE, closeDate=yesterday)]
        return []

    async def _espo_get(path, params=None):
        return {"list": [{"id": "c1", "name": "Kostas", "emailAddress": "kostas@acme.example"}]}

    monkeypatch.setattr(P.espo, "list_all", _list_all)
    monkeypatch.setattr(P.espo, "espo_get", _espo_get)
    monkeypatch.setattr(P.espo, "add_lead_note", _note)

    db = _DB()
    r = await P.post_consult_sweep(session_factory=_factory(db))
    assert r["sent"] == 1
    assert sent and sent[0]["to_addrs"] == ["kostas@acme.example"]
    assert notes and P.POST_CONSULT_MARKER in notes[0][1]


@pytest.mark.asyncio
async def test_post_consult_skips_without_email(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    monkeypatch.setattr(P.espo, "configured", lambda: True)
    sent = _mail_capture(monkeypatch)
    notes = []

    async def _note(oid, text, *, entity="Lead"):
        notes.append(text)
        return True

    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()

    async def _list_all(entity, **kw):
        if entity == "Opportunity":
            return [_opp(stage=P.CONSULT_STAGE, closeDate=yesterday)]
        return []

    async def _espo_get(path, params=None):
        return {"list": []}

    monkeypatch.setattr(P.espo, "list_all", _list_all)
    monkeypatch.setattr(P.espo, "espo_get", _espo_get)
    monkeypatch.setattr(P.espo, "add_lead_note", _note)

    r = await P.post_consult_sweep(session_factory=_factory(_DB()))
    assert r["sent"] == 0 and r["skipped"] == 1
    assert not sent
    assert notes and "no contact email" in notes[0]


# ── proposal_followup_sweep ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followup_disabled(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: _settings(AGENCY_ENABLED=False))
    assert (await F.proposal_followup_sweep())["status"] == "disabled"


@pytest.mark.asyncio
async def test_followup_bump_then_breakup(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)

    # day-4-old proposal → bump (step 1)
    p = _proposal()
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res(rows=[p]))
    r = await F.proposal_followup_sweep(session_factory=_factory(db))
    assert r["sent"] == 1
    assert sent[0]["subject"] == "Re: Audit + plan"
    assert p.meta_data["followup_step"] == "bump"
    assert not p.meta_data.get("followed_out")

    # same proposal at day 15 → break-up + followed_out
    p2 = _proposal(
        sent_at=datetime.now(UTC) - timedelta(days=15),
        meta_data={
            "followup_step": "last_call",
            "followup_history": [{"key": "bump"}, {"key": "last_call"}],
        },
    )
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res(rows=[p2]))
    r = await F.proposal_followup_sweep(session_factory=_factory(db))
    assert r["sent"] == 1 and r["closed_out"] == 1
    assert p2.meta_data["followed_out"] is True


@pytest.mark.asyncio
async def test_followup_skips_replied_and_followed_out(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)
    replied = _proposal(meta_data={"replied": {"at": "x"}})
    closed = _proposal(meta_data={"followed_out": True})
    db = _DB(_Res(rows=[uuid.uuid4()]), _Res(rows=[replied, closed]))
    r = await F.proposal_followup_sweep(session_factory=_factory(db))
    assert r["sent"] == 0 and r["skipped"] == 2
    assert not sent


# ── pm_sweep testimonial ask ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pm_sweep_sends_testimonial_ask(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)
    proj = _project(
        status=AgencyProjectStatus.delivered,
        client_email="buyer@acme.example",
        meta_data={"testimonial_due_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "client_name": "Acme"},
    )
    # active projects query → none; delivered query → proj
    db = _DB(_Res(rows=[]), _Res(rows=[proj]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert r.get("testimonial_asks") == 1
    assert proj.meta_data["testimonial_asked"]
    assert sent and sent[0]["to_addrs"] == ["buyer@acme.example"]


@pytest.mark.asyncio
async def test_pm_sweep_testimonial_once(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)
    proj = _project(
        status=AgencyProjectStatus.delivered,
        client_email="buyer@acme.example",
        meta_data={"testimonial_asked": "2026-11-01T00:00:00"},
    )
    db = _DB(_Res(rows=[]), _Res(rows=[proj]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert not r.get("testimonial_asks")
    assert not sent


# ── crm_engagement proposal-reply stamp ──────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_proposal_replied_stamps_meta(monkeypatch):
    prop = _proposal()
    db = _DB(_Res(rows=[prop]))

    import app.worker._async as A

    monkeypatch.setattr(A, "get_session_factory", lambda: _factory(db))
    hit = await E._mark_proposal_replied(
        "c@acme.example", {"subject": "Re: Audit + plan", "message_id": "m1", "uid": "1"}
    )
    assert hit is True
    assert prop.meta_data["replied"]["message_id"] == "m1"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_mark_proposal_replied_no_match(monkeypatch):
    db = _DB(_Res(rows=[]))

    import app.worker._async as A

    monkeypatch.setattr(A, "get_session_factory", lambda: _factory(db))
    hit = await E._mark_proposal_replied(
        "nobody@acme.example", {"subject": "hi", "message_id": "m2", "uid": "2"}
    )
    assert hit is False
    assert db.commits == 0


# ── client intake loop ──────────────────────────────────────────────────────


def test_kickoff_email_carries_intake_and_loa():
    subj, body = P._kickoff_email("Acme", "Acme — Cloud migration", loa_md="# Letter of Agreement\nterms…")
    assert "reply to this mail" in body
    assert "Admin/hosting + DNS access" in body
    assert "Letter of Agreement" in body
    subj2, body2 = P._kickoff_email(None, "Job")
    assert "Hi there" in body2 and "Letter of Agreement" not in body2


@pytest.mark.asyncio
async def test_pm_sweep_intake_nudge_and_escalation(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)
    kickoff = datetime.now(UTC) - timedelta(days=4)
    proj = _project(kickoff_at=kickoff, due_at=kickoff + timedelta(days=13), milestones=P.project_milestones(), meta_data={"client_name": "Acme"})
    db = _DB(_Res(rows=[proj]), _Res(rows=[]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert r["intake_nudges"] == 1 and r["intake_escalations"] == 1
    assert proj.meta_data["intake_nudged"] and proj.meta_data["intake_escalated"]
    assert sent and "access" in sent[0]["subject"].lower()


@pytest.mark.asyncio
async def test_pm_sweep_intake_received_suppresses(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    sent = _mail_capture(monkeypatch)
    kickoff = datetime.now(UTC) - timedelta(days=4)
    proj = _project(kickoff_at=kickoff, due_at=kickoff + timedelta(days=13), milestones=P.project_milestones(), meta_data={"intake_received": {"at": "x"}})
    db = _DB(_Res(rows=[proj]), _Res(rows=[]))
    r = await P.pm_sweep(session_factory=_factory(db))
    assert not r.get("intake_nudges") and not r.get("intake_escalations")
    assert not sent


@pytest.mark.asyncio
async def test_mark_project_intake_stamps_active_project(monkeypatch):
    proj = _project(status=AgencyProjectStatus.active, client_email="buyer@acme.example")
    db = _DB(_Res(rows=[proj]))

    import app.worker._async as A

    monkeypatch.setattr(A, "get_session_factory", lambda: _factory(db))
    hit = await E._mark_project_intake("buyer@acme.example", {"subject": "Re: Kickoff", "message_id": "m9", "uid": "9"})
    assert hit is True
    assert proj.meta_data["intake_received"]["message_id"] == "m9"

    hit2 = await E._mark_project_intake("buyer@acme.example", {"subject": "again", "message_id": "m10", "uid": "10"})
    assert hit2 is False  # already stamped
