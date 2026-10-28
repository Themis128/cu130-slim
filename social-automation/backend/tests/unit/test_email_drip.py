"""Coverage for app/services/email_drip.py — the autonomous drip loops."""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.services.email_campaigns as S
import app.services.email_drip as D
from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _campaign(**kw):
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), name="Template", kind=CampaignKind.marketing,
        subject="S", text_body="Hi {{first_name}}", html_body="<p>hi</p>",
        from_name="Cloudless", reply_to=None, status=CampaignStatus.draft,
        sent_at=None, espocrm_target_list_id=None, survey_url=None,
        meta_data={"drip_template": "first_approach", "hero_url": "https://x/h.png"},
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


class _Res:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return SimpleNamespace(all=lambda: self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def all(self):
        return self._items


class _DB:
    """Ordered-result fake — execute() pops queued result sets."""

    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.deleted = []

    async def execute(self, stmt, *a, **kw):
        return _Res(self._q.pop(0) if self._q else [])

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        pass


# --- pure helpers ----------------------------------------------------------


def test_parse_dt_variants():
    assert D._parse_dt("2026-10-18T10:00:00+00:00").year == 2026
    assert D._parse_dt("2026-10-18T10:00:00Z").tzinfo is not None
    assert D._parse_dt("2026-10-18").day == 18          # date-only closeDate
    assert D._parse_dt(None) is None
    assert D._parse_dt("not-a-date") is None


def test_anniversary_next_year_boundary():
    start = datetime(2025, 11, 5, 9, 0, tzinfo=UTC)
    anniv = D._anniversary(start, NOW)
    assert anniv == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def test_anniversary_rolls_forward_when_passed():
    start = datetime(2025, 9, 1, tzinfo=UTC)
    anniv = D._anniversary(start, NOW)
    assert anniv == datetime(2027, 9, 1, tzinfo=UTC)


def test_anniversary_leap_day_falls_back():
    start = datetime(2024, 2, 29, tzinfo=UTC)
    anniv = D._anniversary(start, NOW)
    assert (anniv.month, anniv.day) == (2, 28) and anniv.year == 2027


def test_test_address_markers():
    assert D._is_test_address("ci-test+abc@example.invalid")
    assert D._is_test_address("x@test@test")
    assert not D._is_test_address("real@gmail.com")


def test_mailable_excludes_internal_and_synthetic(monkeypatch):
    s = SimpleNamespace(
        DIGEST_EMAIL_TO="tbaltzakis@cloudless.gr",
        SMTP_FROM="noreply@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)
    assert not D._mailable("tbaltzakis@cloudless.gr")      # owner mailbox
    assert not D._mailable("anyone@cloudless.gr")          # own sending domain
    assert not D._mailable("psid.abc@messenger.local")     # synthetic
    assert not D._mailable("not-an-email")
    assert D._mailable("prospect@customer.io")


# --- candidate discovery ---------------------------------------------------


@pytest.mark.asyncio
async def test_first_approach_candidates_skip_tests_and_empties(monkeypatch):
    async def fake_leads():
        return [
            {"id": "L1", "firstName": "Ana", "lastName": "R",
             "emailAddress": "Ana@Real.com"},
            {"id": "L2", "emailAddress": "ci-test+1@example.invalid"},
            {"id": "L3", "emailAddress": "  "},
            {"id": "L4", "firstName": "Bob", "emailAddress": "bob@corp.io"},
        ]

    monkeypatch.setattr(D.espocrm_client, "list_leads_for_campaign", fake_leads)
    out = await D._first_approach_candidates()
    assert [c["email"] for c in out] == ["ana@real.com", "bob@corp.io"]
    assert out[0]["name"] == "Ana R" and out[0]["crm_entity"] == "Lead"


@pytest.mark.asyncio
async def test_renewal_candidates_contact_anniversary(monkeypatch):
    async def fake_contacts():
        return [
            # created ~1y ago → first renewal window (anniv 2026-11-01 ≤ +30d)
            {"id": "C1", "firstName": "Ana", "lastName": "R",
             "emailAddress": "ana@corp.io", "createdAt": "2025-11-01T00:00:00Z"},
            # created recently → no anniversary inside lookahead
            {"id": "C2", "emailAddress": "new@corp.io",
             "createdAt": "2026-10-01T00:00:00Z"},
            # anniversary already passed this year → next is beyond lookahead
            {"id": "C3", "emailAddress": "old@corp.io",
             "createdAt": "2025-01-15T00:00:00Z"},
        ]

    monkeypatch.setattr(D.espocrm_client, "list_contacts", fake_contacts)

    async def no_opps(*a, **kw):
        return []

    monkeypatch.setattr(D.espocrm_client, "list_all", no_opps)
    out = await D._renewal_candidates(NOW)
    assert [c["email"] for c in out] == ["ana@corp.io"]
    assert out[0]["cycle"] == "2026" and out[0]["crm_entity"] == "Contact"


@pytest.mark.asyncio
async def test_renewal_candidates_closed_won_opportunity(monkeypatch):
    async def fake_contacts():
        return [
            {"id": "C9", "firstName": "Kim", "emailAddress": "kim@corp.io",
             "createdAt": "2026-05-01T00:00:00Z"},
        ]

    async def fake_list_all(entity, **kw):
        assert entity == "Opportunity"
        return [
            {"id": "O1", "stage": "Closed Won", "closeDate": "2025-11-10",
             "contactsIds": ["C9"], "accountId": "A1"},
            {"id": "O2", "stage": "Closed Lost", "closeDate": "2025-11-10",
             "contactsIds": ["C9"]},
            {"id": "O3", "stage": "Closed Won", "closeDate": "2024-01-05",
             "contactsIds": ["C9"]},  # anniv 2027-01 — outside lookahead
        ]

    async def fake_accounts():
        return []

    monkeypatch.setattr(D.espocrm_client, "list_contacts", fake_contacts)
    monkeypatch.setattr(D.espocrm_client, "list_all", fake_list_all)
    monkeypatch.setattr(D.espocrm_client, "list_accounts", fake_accounts)
    out = await D._renewal_candidates(NOW)
    assert [c["email"] for c in out] == ["kim@corp.io"]
    assert out[0]["cycle"] == "2026"


# --- clone ------------------------------------------------------------------


def test_clone_carries_content_and_drip_markers():
    tpl = _campaign()
    clone = D._clone(tpl, "first_approach", "2026-10-18", None)
    assert clone.subject == tpl.subject and clone.html_body == tpl.html_body
    assert clone.meta_data["drip_key"] == "first_approach"
    assert clone.meta_data["template_id"] == str(tpl.id)
    assert clone.meta_data["hero_url"] == "https://x/h.png"  # kept from template
    assert "drip_template" not in clone.meta_data
    assert "drip_cycle" not in clone.meta_data


def test_clone_stamps_cycle_when_given():
    clone = D._clone(_campaign(), "renewal", "w", "2027")
    assert clone.meta_data["drip_cycle"] == "2027"


# --- dedupe gate bypass -----------------------------------------------------


def test_duplicate_gate_bypasses_drip_clones():
    async def run():
        class _NoDB:
            async def execute(self, *a, **kw):
                raise AssertionError("drip clones must not reach the similarity scan")

        dup, score = await S.find_duplicate_campaign(
            _NoDB(), _campaign(meta_data={"drip_key": "first_approach"})
        )
        assert dup is None and score == 0.0

    import asyncio

    asyncio.run(run())


# --- full drip wave ---------------------------------------------------------


@pytest.mark.asyncio
async def test_run_drip_mails_only_fresh_addresses(monkeypatch):
    template = _campaign()
    db = _DB(
        results=[
            [template],                       # _template lookup
            [("dup@corp.io",)],               # _already_dripped (cycle=None)
        ]
    )

    captured = {}

    async def fake_resolve(db_, campaign, **kw):
        captured["emails"] = [e["email"] for e in kw["explicit_emails"]]
        return [
            SimpleNamespace(status=RecipientStatus.pending, email=e["email"])
            for e in kw["explicit_emails"]
        ]

    class _Celery:
        @staticmethod
        def send_task(name, args=None):
            captured["task"] = (name, args)

    notes = []

    async def fake_note(cid, note, entity="Lead"):
        notes.append((cid, entity))

    monkeypatch.setattr(S, "resolve_audience", fake_resolve)
    monkeypatch.setattr(
        "app.worker.celery_app.celery_app", _Celery, raising=False
    )
    monkeypatch.setattr(D.espocrm_client, "add_lead_note", fake_note)

    stats = await D._run_drip(
        db, template.team_id, "first_approach",
        [
            {"email": "ana@real.com", "name": "Ana", "crm_entity": "Lead",
             "crm_id": "L1", "cycle": None},
            {"email": "dup@corp.io", "name": "D", "crm_entity": "Lead",
             "crm_id": "L2", "cycle": None},
        ],
        dry_run=False, now=NOW,
    )
    assert stats["mailed"] == 1 and stats["already_sent"] == 1
    assert captured["emails"] == ["ana@real.com"]
    assert captured["task"][0] == "app.worker.tasks.email_campaigns.send_campaign"
    assert notes == [("L1", "Lead")]
    assert db.added and db.added[0].meta_data["drip_key"] == "first_approach"


@pytest.mark.asyncio
async def test_run_drip_dry_run_sends_nothing(monkeypatch):
    template = _campaign()
    db = _DB(results=[[template], []])
    stats = await D._run_drip(
        db, template.team_id, "first_approach",
        [{"email": "a@b.co", "name": "A", "crm_entity": "Lead",
          "crm_id": "L1", "cycle": None}],
        dry_run=True, now=NOW,
    )
    assert stats["mailed"] == 0
    assert stats["would_mail"] == [{"email": "a@b.co", "cycle": None}]
    assert db.added == []


@pytest.mark.asyncio
async def test_run_drip_without_template_reports_error():
    db = _DB(results=[[]])
    stats = await D._run_drip(
        db, uuid.uuid4(), "first_approach",
        [{"email": "a@b.co", "name": "A", "crm_entity": "Lead",
          "crm_id": "L1", "cycle": None}],
        dry_run=False, now=NOW,
    )
    assert stats["errors"] and stats["mailed"] == 0


@pytest.mark.asyncio
async def test_run_drip_all_suppressed_discards_clone(monkeypatch):
    template = _campaign()
    db = _DB(results=[[template], []])

    async def fake_resolve(db_, campaign, **kw):
        return [
            SimpleNamespace(status=RecipientStatus.skipped_suppressed, email="x")
        ]

    monkeypatch.setattr(S, "resolve_audience", fake_resolve)
    stats = await D._run_drip(
        db, template.team_id, "first_approach",
        [{"email": "a@b.co", "name": "A", "crm_entity": "Lead",
          "crm_id": "L1", "cycle": None}],
        dry_run=False, now=NOW,
    )
    assert stats["mailed"] == 0 and stats["suppressed_only"] == 1
    assert db.deleted  # the unsent clone is removed — audit stays clean


# --- AIE course sequence ----------------------------------------------------


@pytest.mark.asyncio
async def test_aie_course_candidates_respect_step_gap(monkeypatch):
    s = SimpleNamespace(
        DIGEST_EMAIL_TO="owner@cloudless.gr",
        SMTP_FROM="noreply@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)
    # step 1 → previous key is first_approach: one mailed 8d ago (due),
    # one 2d ago (still cooling), one with no timestamp (skipped)
    db = _DB(results=[
        [
            ("old@corp.io", NOW - timedelta(days=8), NOW - timedelta(days=8)),
            ("new@corp.io", NOW - timedelta(days=2), NOW - timedelta(days=2)),
            ("notime@corp.io", None, None),
        ],
    ])
    leads = {
        "old@corp.io": {"id": "l-old", "_name": "Old Timer"},
        "new@corp.io": {"id": "l-new", "_name": "New Bee"},
    }
    out = await D._aie_course_candidates(
        db, uuid.uuid4(), "aie_course_1", timedelta(days=7), NOW, leads
    )
    assert [c["email"] for c in out] == ["old@corp.io"]
    assert out[0]["crm_id"] == "l-old" and out[0]["crm_entity"] == "Lead"


@pytest.mark.asyncio
async def test_aie_course_step2_uses_previous_step_key(monkeypatch):
    s = SimpleNamespace(
        DIGEST_EMAIL_TO="owner@cloudless.gr",
        SMTP_FROM="noreply@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)
    calls = []

    async def spy_dates(db, team_id, key):
        calls.append(key)
        return {"grad@corp.io": NOW - timedelta(days=9)}

    monkeypatch.setattr(D, "_dripped_with_dates", spy_dates)
    out = await D._aie_course_candidates(
        _DB(), uuid.uuid4(), "aie_course_2", timedelta(days=7), NOW, {}
    )
    assert calls == ["aie_course_1"]  # gated on step 1, not first_approach
    assert [c["email"] for c in out] == ["grad@corp.io"]
    assert out[0]["crm_id"] is None  # unknown CRM record → note skipped


# --- remaining coverage -----------------------------------------------------


def test_email_drip_enabled_flag(monkeypatch):
    s = SimpleNamespace(EMAIL_DRIP_ENABLED=True)
    monkeypatch.setattr(D, "get_settings", lambda: s)
    monkeypatch.setattr(D.espocrm_client, "configured", lambda: True)
    assert D.email_drip_enabled() is True
    s.EMAIL_DRIP_ENABLED = False
    assert D.email_drip_enabled() is False


def test_parse_dt_date_fallback():
    # datetime.fromisoformat fails, but the 10-char prefix is a valid ISO date
    assert D._parse_dt("2026-10-18 garbage").day == 18
    assert D._parse_dt("@@@@") is None  # both parsers fail


@pytest.mark.asyncio
async def test_already_dripped_cycle_filter():
    db = _DB(results=[[("a@b.co",)]])
    out = await D._already_dripped(db, uuid.uuid4(), "renewal", "2026")
    assert out == {"a@b.co"}
    # cycle path ran a second query variant — covered, result identical shape
    db2 = _DB(results=[[("x@y.co",)]])
    out2 = await D._already_dripped(db2, uuid.uuid4(), "renewal", None)
    assert out2 == {"x@y.co"}


@pytest.mark.asyncio
async def test_dripped_with_dates_naive_and_latest():
    naive = datetime(2026, 10, 1, 8, 0)          # tzinfo None → UTC fix
    aware_old = datetime(2026, 9, 1, tzinfo=UTC)
    aware_new = datetime(2026, 10, 5, tzinfo=UTC)
    db = _DB(
        results=[
            [
                ("a@b.co", naive, None),            # naive → tzified
                ("b@b.co", None, None),             # no date → skip
                ("c@b.co", aware_old, None),
                ("c@b.co", aware_new, None),        # latest wins
            ]
        ]
    )
    out = await D._dripped_with_dates(db, uuid.uuid4(), "aie_course_1")
    assert out["a@b.co"].tzinfo is not None
    assert out["c@b.co"] == aware_new
    assert "b@b.co" not in out


@pytest.mark.asyncio
async def test_renewal_skips_unparseable_and_test_contacts(monkeypatch):
    async def fake_contacts():
        return [
            {"id": "C1", "emailAddress": "a@corp.io"},          # no createdAt
            {"id": "C2", "emailAddress": "test@example.com",    # not mailable
             "createdAt": "2025-11-01T00:00:00Z"},
            {"id": "C3", "firstName": "Ok", "lastName": "Name",
             "emailAddress": "ok@corp.io",
             "createdAt": "2025-11-01T00:00:00Z"},
        ]

    async def no_opps(*a, **kw):
        return []

    monkeypatch.setattr(D.espocrm_client, "list_contacts", fake_contacts)
    monkeypatch.setattr(D.espocrm_client, "list_all", no_opps)
    out = await D._renewal_candidates(NOW)
    assert [c["email"] for c in out] == ["ok@corp.io"]


@pytest.mark.asyncio
async def test_renewal_opportunity_pull_failure_tolerated(monkeypatch):
    async def fake_contacts():
        return []

    async def boom(*a, **kw):
        raise RuntimeError("crm down")

    monkeypatch.setattr(D.espocrm_client, "list_contacts", fake_contacts)
    monkeypatch.setattr(D.espocrm_client, "list_all", boom)
    assert await D._renewal_candidates(NOW) == []


@pytest.mark.asyncio
async def test_renewal_account_pull_failure_and_account_fallback(monkeypatch):
    async def fake_contacts():
        return []

    async def opps(*a, **kw):
        return [
            {"id": "O1", "stage": "Closed Won", "closeDate": "2025-11-10",
             "accountId": "A9", "contactsIds": ["C-ghost"]},  # unknown contact
            {"id": "O2", "stage": "Closed Won"},             # no closeDate
        ]

    async def boom_accounts():
        raise RuntimeError("accounts down")

    monkeypatch.setattr(D.espocrm_client, "list_contacts", fake_contacts)
    monkeypatch.setattr(D.espocrm_client, "list_all", opps)
    monkeypatch.setattr(D.espocrm_client, "list_accounts", boom_accounts)
    # account pull fails → account_by_id empty → no candidates, no crash
    assert await D._renewal_candidates(NOW) == []

    # account pull works → account-email fallback row
    async def ok_accounts():
        return [{"id": "A9", "name": "Acme", "emailAddress": "ops@acme.io"}]

    monkeypatch.setattr(D.espocrm_client, "list_accounts", ok_accounts)
    out = await D._renewal_candidates(NOW)
    assert [c["email"] for c in out] == ["ops@acme.io"]
    assert out[0]["crm_entity"] == "Account" and out[0]["crm_id"] == "A9"


@pytest.mark.asyncio
async def test_aie_lead_map_and_unmailable_step_candidate(monkeypatch):
    async def leads():
        return [
            {"id": "L1", "firstName": "Ana", "emailAddress": "ana@corp.io"},
            {"id": "L2", "accountName": "Acme"},  # no email → skipped
        ]

    monkeypatch.setattr(D.espocrm_client, "list_leads_for_campaign", leads)
    m = await D._aie_lead_map()
    assert m == {"ana@corp.io": {"id": "L1", "_name": "Ana"}}

    async def boom():
        raise RuntimeError("crm down")

    monkeypatch.setattr(D.espocrm_client, "list_leads_for_campaign", boom)
    assert await D._aie_lead_map() == {}

    # mailed_prev hit but address is not mailable → skipped
    async def dates(db, team_id, key):
        return {
            "test@example.com": datetime(2026, 1, 1, tzinfo=UTC),
            "real@corp.io": datetime(2026, 1, 1, tzinfo=UTC),
        }

    monkeypatch.setattr(D, "_dripped_with_dates", dates)
    monkeypatch.setattr(
        D.espocrm_client, "list_leads_for_campaign",
        lambda: iter([]),
    )
    s = SimpleNamespace(
        EMAIL_DRIP_INTERNAL_DOMAINS=(), EMAIL_DRIP_EXCLUDE=(),
        DIGEST_EMAIL_TO="ops@cloudless.gr", SMTP_FROM="no@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)
    out = await D._aie_course_candidates(
        _DB(), uuid.uuid4(), "aie_course_1", timedelta(days=7), NOW,
        {"real@corp.io": {"id": "L9", "_name": "Real"}},
    )
    assert [c["email"] for c in out] == ["real@corp.io"]
    assert out[0]["crm_id"] == "L9"


@pytest.mark.asyncio
async def test_run_drip_empty_candidates_and_all_mailed():
    stats = await D._run_drip(
        _DB(), uuid.uuid4(), "first_approach", [], dry_run=False, now=NOW
    )
    assert stats["candidates"] == 0 and stats["mailed"] == 0

    template = _campaign()
    db = _DB(results=[[template], [("dup@corp.io",)]])
    stats = await D._run_drip(
        db, template.team_id, "first_approach",
        [{"email": "dup@corp.io", "name": "D", "crm_entity": "Lead",
          "crm_id": "L1", "cycle": None}],
        dry_run=False, now=NOW,
    )
    assert stats["mailed"] == 0 and stats["already_sent"] == 1
    assert db.added == []  # clone never created


@pytest.mark.asyncio
async def test_run_drip_note_skip_and_failure(monkeypatch):
    template = _campaign()
    db = _DB(results=[[template], []])  # nobody already mailed

    async def fake_resolve(db_, campaign, **kw):
        return [
            SimpleNamespace(status=RecipientStatus.pending, email="no@cand.io"),
            SimpleNamespace(status=RecipientStatus.pending, email="real@corp.io"),
        ]

    class _Celery:
        @staticmethod
        def send_task(name, args=None):
            pass

    async def boom(cid, note, entity="Lead"):
        raise RuntimeError("espo down")

    monkeypatch.setattr(S, "resolve_audience", fake_resolve)
    monkeypatch.setattr(
        "app.worker.celery_app.celery_app", _Celery, raising=False
    )
    monkeypatch.setattr(D.espocrm_client, "add_lead_note", boom)

    stats = await D._run_drip(
        db, template.team_id, "first_approach",
        [{"email": "real@corp.io", "name": "R", "crm_entity": "Lead",
          "crm_id": "L1", "cycle": None}],
        dry_run=False, now=NOW,
    )
    # rec 'no@cand.io' has no candidate → skipped; 'real@corp.io' note fails
    # but the drip still reports the queue.
    assert stats["mailed"] == 2


class _SessionCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_run_email_drip_sweep_error_isolation_and_slack(monkeypatch):
    import app.db.session as dbsession
    import app.services.slack_notifications as slack_mod

    s = SimpleNamespace(
        EMAIL_DRIP_AIE_ENABLED=False, EMAIL_DRIP_AIE_GAP_DAYS=7,
        EMAIL_DRIP_RENEWAL_LOOKAHEAD_DAYS=30,
        DIGEST_EMAIL_TO="ops@cloudless.gr", SMTP_FROM="no@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)

    team = SimpleNamespace(id=uuid.uuid4())

    class _DBFail(_DB):
        async def execute(self, stmt, *a, **kw):
            if not self._q:
                raise RuntimeError("db exploded")
            return await super().execute(stmt, *a, **kw)

    db = _DBFail(results=[[team]])
    monkeypatch.setattr(
        dbsession, "async_session_maker", lambda: _SessionCM(db)
    )

    async def leads():
        return [{"id": "L1", "firstName": "Ana",
                 "emailAddress": "ana@corp.io"}]

    async def no_contacts():
        return []

    async def no_opps(*a, **kw):
        return []

    monkeypatch.setattr(D.espocrm_client, "list_leads_for_campaign", leads)
    monkeypatch.setattr(D.espocrm_client, "list_contacts", no_contacts)
    monkeypatch.setattr(D.espocrm_client, "list_all", no_opps)

    sent = []

    async def fake_slack(text):
        sent.append(text)

    monkeypatch.setattr(slack_mod, "post_digest_text_to_slack", fake_slack)

    report = await D.run_email_drip_sweep()
    assert report["teams"] and report["teams"][0]["team_id"] == str(team.id)
    fa = report["teams"][0][D.DRIP_FIRST_APPROACH]
    assert fa["errors"] and "db exploded" in fa["errors"][0]
    assert sent and "Email drip sweep" in sent[0] and "errors" in sent[0]


@pytest.mark.asyncio
async def test_slack_report_tolerates_post_failure(monkeypatch):
    import app.services.slack_notifications as slack_mod

    async def boom(text):
        raise RuntimeError("slack down")

    monkeypatch.setattr(slack_mod, "post_digest_text_to_slack", boom)
    report = {
        "teams": [
            {
                "team_id": "t",
                "first_approach": None,          # falsy → skipped in report
                "renewal": {"mailed": 2, "candidates": 3, "already_sent": 1},
            }
        ]
    }
    await D._slack_report(report)  # no raise despite slack being down


@pytest.mark.asyncio
async def test_run_email_drip_sweep_aie_enabled(monkeypatch):
    import app.db.session as dbsession

    s = SimpleNamespace(
        EMAIL_DRIP_AIE_ENABLED=True, EMAIL_DRIP_AIE_GAP_DAYS=7,
        EMAIL_DRIP_RENEWAL_LOOKAHEAD_DAYS=30,
        DIGEST_EMAIL_TO="ops@cloudless.gr", SMTP_FROM="no@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)

    team = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[[team]])
    monkeypatch.setattr(
        dbsession, "async_session_maker", lambda: _SessionCM(db)
    )

    async def none(*a, **kw):
        return []

    async def empty_map():
        return {}

    monkeypatch.setattr(D, "_aie_lead_map", empty_map)
    monkeypatch.setattr(D, "_first_approach_candidates", none)
    monkeypatch.setattr(D, "_renewal_candidates", none)
    monkeypatch.setattr(D, "_aie_course_candidates", none)

    report = await D.run_email_drip_sweep(dry_run=True)
    ts = report["teams"][0]
    for key in (D.DRIP_FIRST_APPROACH, D.DRIP_RENEWAL, *D.DRIP_AIE_STEPS):
        assert key in ts


@pytest.mark.asyncio
async def test_run_email_drip_sweep_writes_audit_artifact(monkeypatch):
    """Every sweep — even a quiet one — leaves an audit_logs sweep row so the
    workforce audit can read worker liveness instead of sent-mail volume."""
    import app.db.session as dbsession
    from app.models.user import AuditLog

    s = SimpleNamespace(
        EMAIL_DRIP_AIE_ENABLED=False, EMAIL_DRIP_AIE_GAP_DAYS=7,
        EMAIL_DRIP_RENEWAL_LOOKAHEAD_DAYS=30,
        DIGEST_EMAIL_TO="ops@cloudless.gr", SMTP_FROM="no@cloudless.gr",
    )
    monkeypatch.setattr(D, "get_settings", lambda: s)

    team = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[[team]])
    monkeypatch.setattr(
        dbsession, "async_session_maker", lambda: _SessionCM(db)
    )

    async def none(*a, **kw):
        return []

    monkeypatch.setattr(D, "_first_approach_candidates", none)
    monkeypatch.setattr(D, "_renewal_candidates", none)

    report = await D.run_email_drip_sweep(dry_run=True)
    artifacts = [o for o in db.added if isinstance(o, AuditLog)]
    assert len(artifacts) == 1
    assert artifacts[0].action == "email-drip"
    assert artifacts[0].resource_type == "sweep"
    assert artifacts[0].team_id == team.id
    assert "mailed=0" in artifacts[0].detail
    assert report["mailed"] == 0
