"""CRM hygiene — dedupe/quarantine/SLA logic against a fake EspoCRM boundary."""

from __future__ import annotations

import pytest

from app.services import crm_hygiene


def _lead(id_: str, email: str, status: str = "New", created: str = "2026-10-01T10:00:00Z", name: str = "x", assigned: str | None = None):
    return {
        "id": id_,
        "name": name,
        "emailAddress": email,
        "status": status,
        "source": "Web Site",
        "createdAt": created,
        "assignedUserId": assigned,
    }


class _FakeEspo:
    def __init__(self, leads, user: dict | None = None):
        self.leads = leads
        self.user = user or {}
        self.patched: list[tuple[str, dict]] = []
        self.notes: list[tuple[str, str]] = []

    async def list_all(self, entity, **_kw):
        return self.leads

    async def espo_get(self, path, params=None):
        assert path == "/App/user"
        return {"user": self.user}

    async def espo_patch(self, path, payload):
        self.patched.append((path, payload))
        return {}

    async def add_lead_note(self, lead_id, note, **_kw):
        self.notes.append((lead_id, note))
        return True

    def configured(self):
        return True


def _wire(monkeypatch, fake: _FakeEspo):
    monkeypatch.setattr(crm_hygiene.espocrm_client, "list_all", fake.list_all)
    monkeypatch.setattr(crm_hygiene.espocrm_client, "espo_get", fake.espo_get)
    monkeypatch.setattr(crm_hygiene.espocrm_client, "espo_patch", fake.espo_patch)
    monkeypatch.setattr(crm_hygiene.espocrm_client, "add_lead_note", fake.add_lead_note)
    monkeypatch.setattr(crm_hygiene.espocrm_client, "configured", fake.configured)


@pytest.mark.asyncio
async def test_quarantines_ci_test_leads(monkeypatch):
    fake = _FakeEspo([
        _lead("a", "ci-test+abc@example.invalid", name="CI Functional Test"),
        _lead("b", "real@customer.gr"),
    ])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["quarantined"] == 1
    assert fake.patched == [("/Lead/a", {"status": "Dead"})]


@pytest.mark.asyncio
async def test_preserves_messenger_synthetic_lead(monkeypatch):
    fake = _FakeEspo([_lead("m", "psid.123@messenger.local")])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["quarantined"] == 0
    assert fake.patched == []


@pytest.mark.asyncio
async def test_dedupes_same_email_keeps_newest(monkeypatch):
    fake = _FakeEspo([
        _lead("old", "dup@x.gr", created="2026-09-01T10:00:00Z"),
        _lead("new", "Dup@x.gr ", created="2026-10-10T10:00:00Z"),
    ])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["deduped"] == 1
    assert fake.patched == [("/Lead/old", {"status": "Dead"})]


@pytest.mark.asyncio
async def test_stale_new_leads_surfaced_but_untouched(monkeypatch):
    fake = _FakeEspo([
        _lead("stale", "stale@x.gr", created="2026-01-01T00:00:00Z"),
        _lead("fresh", "fresh@x.gr", created="2999-01-01T00:00:00Z"),
        _lead("dead", "dead@x.gr", status="Dead", created="2026-01-01T00:00:00Z"),
    ])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert [s["id"] for s in stats["stale_new"]] == ["stale"]
    assert fake.patched == []


@pytest.mark.asyncio
async def test_dead_leads_not_reprocessed(monkeypatch):
    fake = _FakeEspo([_lead("d", "ci-test+x@example.invalid", status="Dead")])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["quarantined"] == 0
    assert fake.patched == []


@pytest.mark.asyncio
async def test_internal_self_domain_quarantined(monkeypatch):
    fake = _FakeEspo([
        _lead("self", "tbaltzakis@cloudless.gr"),
        _lead("real", "real@customer.gr"),
    ])
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["quarantined"] == 1
    assert fake.patched == [("/Lead/self", {"status": "Dead"})]
    assert "internal/self" in fake.notes[0][1]


@pytest.mark.asyncio
async def test_unassigned_leads_get_owner(monkeypatch):
    fake = _FakeEspo(
        [
            _lead("new", "new@x.gr"),
            _lead("owned", "owned@x.gr", assigned="someone-else"),
            _lead("dead", "dead@x.gr", status="Dead"),
        ],
        user={"id": "u-api"},
    )
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["assigned"] == 1
    assert fake.patched == [("/Lead/new", {"assignedUserId": "u-api"})]


@pytest.mark.asyncio
async def test_no_assignee_resolved_skips_assignment(monkeypatch):
    fake = _FakeEspo([_lead("new", "new@x.gr")])  # no user → resolution returns None
    _wire(monkeypatch, fake)
    stats = await crm_hygiene.run_crm_hygiene(alert=False)
    assert stats["assigned"] == 0
    assert fake.patched == []
