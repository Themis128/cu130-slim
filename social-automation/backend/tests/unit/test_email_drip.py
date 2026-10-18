"""Coverage for app/services/email_drip.py — the autonomous drip loops."""
import uuid
from datetime import UTC, datetime
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
