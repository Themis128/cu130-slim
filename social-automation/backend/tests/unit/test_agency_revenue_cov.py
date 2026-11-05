"""Coverage for the revenue-loop additions — token expiry sweep, the three
new agency task wrappers, autosend pass + payment-url mint, proposal
follow-up edge branches, and the crm_engagement project matchers.

Same fake-DB queue pattern as test_agency_funnel.py.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import app.services.agency_pipeline as P
import app.services.crm_engagement as E
import app.services.proposal_followup as F
import app.worker.tasks.agency as AT
import app.worker.tasks.token_expiry as TE
from app.models.agency import AgencyAuditStatus, AgencyProjectStatus, AgencyProposalStatus


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
    base = dict(AGENCY_ENABLED=True, AGENCY_AUTOSEND_DAILY_CAP=5,
                AGENCY_CHECKOUT_URL="", CLOUDLESS_LEADS_WEBHOOK_SECRET="s3cret")
    base.update(kw)
    return SimpleNamespace(**base)


# ── token_expiry sweep ───────────────────────────────────────────────────────


def _acct(platform, meta, username="u"):
    return SimpleNamespace(id=uuid.uuid4(), platform=platform, meta_data=meta, username=username)


@pytest.mark.asyncio
async def test_token_expiry_sweep_buckets(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        _acct("facebook", {"expires_at": (now + timedelta(days=3)).isoformat()}),          # warn
        _acct("linkedin", {"token_expires_at": (now - timedelta(hours=1)).isoformat()}),   # reauth
        _acct("x", {"expires_at": now.replace(tzinfo=None).isoformat()}),                  # naive → reauth (x non-refreshable, exp<=now? borderline)
        _acct("tiktok", {}),                                                               # no expiry → skip
        _acct("meta", {"expires_at": "not-a-date"}),                                      # bad iso → skip
        _acct("unknownplat", {"expires_at": (now - timedelta(days=1)).isoformat()}),      # no lead → skip
        _acct("instagram", {"expires_at": (now + timedelta(days=30)).isoformat()}),       # beyond lead → skip
        _acct("threads", {"expires_at": (now + timedelta(hours=2)).isoformat()}),         # warn
    ]
    db = _DB(_Res(rows=rows))
    monkeypatch.setattr(TE, "task_session", lambda: _CM(db))
    report = await TE._sweep()
    warns = {e["platform"] for e in report["warn"]}
    reauths = {e["platform"] for e in report["reauth"]}
    assert "facebook" in warns and "threads" in warns
    assert "linkedin" in reauths
    assert "tiktok" not in warns and "tiktok" not in reauths
    assert "unknownplat" not in warns and "unknownplat" not in reauths


@pytest.mark.asyncio
async def test_token_expiry_post_and_task(monkeypatch):
    import app.services.slack_notifications as SN

    posted = []

    async def _post(text):
        posted.append(text)

    monkeypatch.setattr(SN, "post_alert_to_slack", _post)
    await TE._post({"warn": [], "reauth": []})
    assert posted == []
    await TE._post({"warn": [{"platform": "fb", "account": "a", "expires": "2026-01-01T00:00"}],
                    "reauth": [{"platform": "li", "account": "b", "expires": "2026-01-01T00:00"}]})
    assert len(posted) == 1 and "Token expiry sweep" in posted[0] and "EXPIRED" in posted[0]

    # task wrapper — run_async drives the coroutines on a fresh loop
    now = datetime.now(UTC)
    db = _DB(_Res(rows=[_acct("facebook", {"expires_at": (now + timedelta(days=2)).isoformat()})]))
    monkeypatch.setattr(TE, "task_session", lambda: _CM(db))
    monkeypatch.setattr(TE, "run_async", lambda c: asyncio.run(c))
    out = TE.run_token_expiry_sweep()
    assert out["status"] == "ok" and out["warn"] == 1 and out["reauth"] == 0


# ── agency task wrappers (followup / won / post-consult) ─────────────────────


def _task_settings(monkeypatch, enabled):
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(AGENCY_ENABLED=enabled))


def test_agency_followup_task_paths(monkeypatch):
    import app.services.proposal_followup as PF

    monkeypatch.setattr(AT, "run_async", lambda c: asyncio.run(c))
    pf = AsyncMock(return_value={"status": "ok", "sent": 1})
    monkeypatch.setattr(PF, "proposal_followup_sweep", pf)

    _task_settings(monkeypatch, False)
    assert AT.run_agency_proposal_followup_sweep() is None
    pf.assert_not_awaited()

    _task_settings(monkeypatch, True)
    assert AT.run_agency_proposal_followup_sweep() == {"status": "ok", "sent": 1}
    pf.assert_awaited_once()

    pf.side_effect = Exception("boom")
    assert AT.run_agency_proposal_followup_sweep() is None


def test_agency_won_task_paths(monkeypatch):
    monkeypatch.setattr(AT, "run_async", lambda c: asyncio.run(c))
    won = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(P, "won_sweep", won)

    _task_settings(monkeypatch, False)
    assert AT.run_agency_won_sweep() is None

    _task_settings(monkeypatch, True)
    assert AT.run_agency_won_sweep() == {"status": "ok"}
    won.assert_awaited_once()

    won.side_effect = Exception("boom")
    assert AT.run_agency_won_sweep() is None


def test_agency_post_consult_task_paths(monkeypatch):
    monkeypatch.setattr(AT, "run_async", lambda c: asyncio.run(c))
    pc = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(P, "post_consult_sweep", pc)

    _task_settings(monkeypatch, False)
    assert AT.run_agency_post_consult_sweep() is None

    _task_settings(monkeypatch, True)
    assert AT.run_agency_post_consult_sweep() == {"status": "ok"}
    pc.assert_awaited_once()

    pc.side_effect = Exception("boom")
    assert AT.run_agency_post_consult_sweep() is None


# ── proposal_followup edge branches ──────────────────────────────────────────


def _proposal(**kw):
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), audit_id=None, lead_id=None,
        espo_lead_id=None, recipient_email="c@acme.example", recipient_name="Acme",
        services=["cloud"], price_low_eur=100, price_high_eur=200,
        subject="Audit + plan", body_md="b", meta_data={},
        status=AgencyProposalStatus.sent,
        sent_at=datetime.now(UTC) - timedelta(days=4),
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.mark.asyncio
async def test_followup_factory_default_and_no_team(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("app.worker._async.get_session_factory", lambda: sentinel)
    assert F._factory(None) is sentinel
    custom = lambda: None  # noqa: E731
    assert F._factory(custom) is custom

    monkeypatch.setattr(F, "get_settings", lambda: _settings())
    db = _DB(_Res())  # team query → empty
    out = await F.proposal_followup_sweep(session_factory=_factory(db))
    assert out == {"status": "skipped", "reason": "no_team"}


@pytest.mark.asyncio
async def test_followup_edge_branches(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: _settings())
    sent = []

    async def _send(**kw):
        sent.append(kw)

    monkeypatch.setattr("app.services.email_digest.send_email", _send)
    import app.services.espocrm_client as espo
    notes = []

    async def _note(oid, text):
        notes.append((oid, text))

    monkeypatch.setattr(espo, "add_lead_note", _note)
    import app.services.slack_notifications as SN

    async def _slack_fail(text):
        raise RuntimeError("slack down")

    monkeypatch.setattr(SN, "post_digest_text_to_slack", _slack_fail)

    now = datetime.now(UTC)
    p_no_sent = _proposal(sent_at=None)
    p_future_naive = _proposal(sent_at=(now + timedelta(days=2)).replace(tzinfo=None))
    p_closed = _proposal(
        sent_at=now - timedelta(days=20),
        meta_data={"followup_history": [{"key": "bump"}, {"key": "last_call"}, {"key": "breakup"}]},
    )
    p_send = _proposal(espo_lead_id="lead-1")
    team_id = uuid.uuid4()
    db = _DB(_Res(rows=[team_id]), _Res(rows=[p_no_sent, p_future_naive, p_closed, p_send]))

    out = await F.proposal_followup_sweep(session_factory=_factory(db))
    assert out["sent"] == 1 and out["closed_out"] == 1 and out["skipped"] == 2
    assert len(sent) == 1 and "Re: Audit + plan" == sent[0]["subject"]
    assert notes and notes[0][0] == "lead-1"
    assert p_closed.meta_data["followed_out"] is True
    assert p_send.meta_data["followup_step"] == "bump"


# ── crm_engagement project matchers + sweep arms ─────────────────────────────


def _project(**kw):
    d = dict(id=uuid.uuid4(), team_id=uuid.uuid4(), name="Acme job",
             client_email="c@acme.example", status=AgencyProjectStatus.active,
             meta_data={}, milestones=[], created_at=datetime.now(UTC))
    d.update(kw)
    return SimpleNamespace(**d)


def _cand(**kw):
    d = {"from": "c@acme.example", "subject": "quick question", "message_id": "m1", "uid": "u1"}
    d.update(kw)
    return d


def _patch_session_factory(monkeypatch, db):
    monkeypatch.setattr("app.worker._async.get_session_factory", lambda: _factory(db))


@pytest.mark.asyncio
async def test_crm_mark_proposal_and_intake(monkeypatch):
    prop = _proposal()
    proj = _project()
    # each matcher opens its own session — queue per call via side_effect factory
    calls = iter([_DB(_Res(rows=[prop])), _DB(_Res(rows=[proj]))])
    monkeypatch.setattr("app.worker._async.get_session_factory",
                        lambda: (lambda: _CM(next(calls))))
    assert await E._mark_proposal_replied("c@acme.example", _cand()) is True
    assert prop.meta_data["replied"]["subject"] == "quick question"
    assert await E._mark_project_intake("c@acme.example", _cand(subject="here are the files")) is True
    assert proj.meta_data["intake_received"]["subject"] == "here are the files"


@pytest.mark.asyncio
async def test_crm_support_case_paths(monkeypatch):
    import app.services.espocrm_client as espo

    proj = _project()
    _patch_session_factory(monkeypatch, _DB(_Res(rows=[proj])))
    post = AsyncMock(return_value={"id": "case-1"})
    monkeypatch.setattr(espo, "espo_post", post)

    assert await E._project_support_case("c@acme.example", _cand(subject="no keyword here")) is False
    post.assert_not_awaited()

    assert await E._project_support_case("c@acme.example", _cand(subject="site is down — urgent")) is True
    assert proj.meta_data["support_cases"][0]["case_id"] == "case-1"

    post.side_effect = Exception("espo down")
    _patch_session_factory(monkeypatch, _DB(_Res(rows=[proj])))
    assert await E._project_support_case("c@acme.example", _cand(subject="another urgent issue")) is False


@pytest.mark.asyncio
async def test_crm_testimonial_stamp(monkeypatch):
    proj = _project(status=AgencyProjectStatus.delivered,
                    meta_data={"testimonial_asked": True, "client_name": "Acme"})
    _patch_session_factory(monkeypatch, _DB(_Res(rows=[proj])))
    assert await E._mark_project_testimonial("c@acme.example", _cand(subject="great work team")) is True
    assert proj.meta_data["testimonial_received"]["subject"] == "great work team"
    assert proj.meta_data["proof_card"]["project"] == "Acme job"


@pytest.mark.asyncio
async def test_crm_engagement_sweep_arms(monkeypatch):
    import app.services.espocrm_client as espo
    import app.services.slack_notifications as SN

    monkeypatch.setattr(E, "get_settings", lambda: SimpleNamespace(
        CRM_ENGAGEMENT_ENABLED=True, CRM_ENGAGEMENT_LOOKBACK_DAYS=7,
        SMTP_FROM="bot@cloudless.gr"))
    monkeypatch.setattr(E, "_fetch_candidates", lambda days: ([_cand()], []))
    monkeypatch.setattr(E, "_crm_emails", AsyncMock(return_value={}))
    monkeypatch.setattr(espo, "target_list_id", AsyncMock(return_value="list-1"))
    for name in ("_mark_proposal_replied", "_mark_project_intake",
                 "_mark_project_testimonial", "_project_support_case"):
        monkeypatch.setattr(E, name, AsyncMock(return_value=True))
    slack = []

    async def _slack(text):
        slack.append(text)

    monkeypatch.setattr(SN, "post_digest_text_to_slack", _slack)

    stats = await E.run_crm_engagement_sweep(dry_run=False)
    assert stats["proposal_replies"] == 1 and stats["intake_received"] == 1
    assert stats["testimonials"] == 1 and stats["support_cases"] == 1
    assert slack and "proposal reply" in slack[0]


@pytest.mark.asyncio
async def test_crm_slack_report_details(monkeypatch):
    import app.services.slack_notifications as SN

    posted = []

    async def _slack(text):
        posted.append(text)

    monkeypatch.setattr(SN, "post_digest_text_to_slack", _slack)
    await E._slack_report({
        "matched": 1, "noted": 1, "status_bumped": 0, "errors": 0, "dry_run": False,
        "details": [{"email": "a@b.c", "subject": "hi"}],
        "proposal_reply_details": [{"email": "x@y.z", "subject": "re: audit"}],
    })
    assert posted and "a@b.c" in posted[0] and "x@y.z" in posted[0]

    # silent path — nothing to report
    posted.clear()
    await E._slack_report({"matched": 0, "noted": 0, "status_bumped": 0,
                           "errors": 0, "dry_run": False, "details": [],
                           "proposal_reply_details": []})
    assert posted == []


# ── agency_pipeline autosend pass + payment url ──────────────────────────────


def _draft(**kw):
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), audit_id=uuid.uuid4(), lead_id=None,
        espo_lead_id=None, recipient_email="buyer@acme-industries.com",
        recipient_name="Acme", services=["cloud"], price_low_eur=1200,
        price_high_eur=2000, subject="s", body_md="b", meta_data={},
        status=AgencyProposalStatus.draft, sent_at=None,
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.mark.asyncio
async def test_autosend_pass_sends_clean_holds_dirty(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings())
    import app.services.slack_notifications as SN

    async def _alert(text):
        pass

    monkeypatch.setattr(SN, "post_alert_to_slack", _alert)
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(P, "send_proposal", send)

    ok = _draft()
    held = _draft(recipient_email="not-an-email")
    audit = SimpleNamespace(status=AgencyAuditStatus.completed)
    db = _DB(
        _Res(rows=[ok, held]),          # drafts
        _Res(rows=[]),                  # already-sent-today
        _Res(scalar=audit),             # ok.audit
        _Res(scalar=audit),             # held.audit
    )
    stats = await P._autosend_pass(db, uuid.uuid4())
    assert stats["evaluated"] == 2 and stats["sent"] == 1 and stats["held"] == 1
    assert stats["held_reasons"].get("no-valid-recipient") == 1
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_autosend_pass_send_error_and_cap(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: _settings(AGENCY_AUTOSEND_DAILY_CAP=0))
    send = AsyncMock(return_value={"ok": False, "error": "smtp"})
    monkeypatch.setattr(P, "send_proposal", send)
    ok = _draft()
    audit = SimpleNamespace(status=AgencyAuditStatus.completed)
    db = _DB(_Res(rows=[ok]), _Res(rows=[]), _Res(scalar=audit))
    stats = await P._autosend_pass(db, uuid.uuid4())
    # daily-cap 0 → held before send
    assert stats["held"] == 1 and stats["held_reasons"].get("daily-cap") == 1
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_agency_payment_url_paths(monkeypatch):
    prop = _proposal()

    monkeypatch.setattr(P, "get_settings", lambda: _settings(AGENCY_CHECKOUT_URL=""))
    assert await P._agency_payment_url(prop, 100) is None
    monkeypatch.setattr(P, "get_settings", lambda: _settings(AGENCY_CHECKOUT_URL="https://x", ))
    assert await P._agency_payment_url(prop, 0) is None

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"url": "https://pay.example/checkout/1"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await P._agency_payment_url(prop, 500) == "https://pay.example/checkout/1"

    class _BadClient(_Client):
        async def post(self, *a, **k):
            raise RuntimeError("net down")

    monkeypatch.setattr(httpx, "AsyncClient", _BadClient)
    assert await P._agency_payment_url(prop, 500) is None
