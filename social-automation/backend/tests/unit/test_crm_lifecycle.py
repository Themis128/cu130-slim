"""CRM lifecycle — deterministic scoring + guarded hot-lead conversion."""

from __future__ import annotations

import pytest

from app.services import crm_lifecycle


def _lead(
    id_: str = "x",
    email: str = "owner@company.gr",
    status: str = "New",
    name: str = "Owner",
    account: str | None = None,
    website: str | None = None,
    description: str = "",
    source: str = "Web Site",
    assigned: str | None = "u-1",
):
    return {
        "id": id_,
        "name": name,
        "emailAddress": email,
        "status": status,
        "source": source,
        "accountName": account,
        "website": website,
        "description": description,
        "assignedUserId": assigned,
        "createdAt": "2026-10-01T10:00:00Z",
    }


def _note(post: str, created: str = "2026-10-10T10:00:00Z"):
    return {"id": "n1", "post": post, "createdAt": created}


def test_score_bare_lead_is_cold():
    score, band, _ = crm_lifecycle.score_lead(_lead(email="x@gmail.com"), [])
    assert band == "cold"
    assert score < crm_lifecycle.WARM_THRESHOLD


def test_score_rich_lead_reaches_hot():
    lead = _lead(
        account="Acme SA",
        website="acme.gr",
        description=(
            "We want to migrate our hosting and need a proposal. "
            "budget allocated for Q4 launch — timeline by December. " * 3
            + "\nutm_medium: cpc\nutm_campaign: oct-audit"
        ),
    )
    score, band, reasons = crm_lifecycle.score_lead(lead, [])
    assert band == "hot"
    assert score >= crm_lifecycle.HOT_THRESHOLD
    assert any("paid campaign" in r for r in reasons)


def test_reply_note_pushes_score_up():
    base = crm_lifecycle.score_lead(_lead(email="x@gmail.com"), [])
    replied = crm_lifecycle.score_lead(
        _lead(email="x@gmail.com"), [_note("Reply received: hi · msg <a@b>")]
    )
    assert replied[0] - base[0] == 25


def test_business_email_domain_scores():
    _, _, reasons = crm_lifecycle.score_lead(_lead(email="a@acme.gr"), [])
    assert any("business email domain" in r for r in reasons)


def test_synthetic_never_converts():
    messenger = _lead(email="psid.1@messenger.local")
    test = _lead(email="ci-test+x@example.invalid")
    selflead = _lead(email="me@cloudless.gr")
    assert crm_lifecycle._is_synthetic(messenger)
    assert crm_lifecycle._is_synthetic(test)
    assert crm_lifecycle._is_synthetic(selflead)
    assert not crm_lifecycle._is_synthetic(_lead())


def test_needs_score_markers():
    assert crm_lifecycle._needs_score([]) is True
    scored = [_note("Lead score: 40/100 (warm)", "2026-10-10T10:00:00Z")]
    assert crm_lifecycle._needs_score(scored) is False
    # reply newer than the last score → re-score
    newer = scored + [_note("Reply received: ok", "2026-10-12T10:00:00Z")]
    assert crm_lifecycle._needs_score(newer) is True
    # reply older than the score → no re-score
    older = scored + [_note("Reply received: ok", "2026-10-09T10:00:00Z")]
    assert crm_lifecycle._needs_score(older) is False


class _FakeEspo:
    def __init__(self, leads, notes_by_lead=None, convert_result=None):
        self.leads = leads
        self.notes_by_lead = notes_by_lead or {}
        self.convert_result = convert_result or {"createdContactId": "c1", "createdOpportunityId": "o1"}
        self.posted: list[tuple[str, dict]] = []
        self.notes: list[tuple[str, str]] = []

    async def list_all(self, entity, **kw):
        if entity == "Note":
            parent_id = next(
                (w["value"] for w in kw.get("where", []) if w.get("attribute") == "parentId"),
                None,
            )
            return self.notes_by_lead.get(parent_id, [])
        return self.leads

    async def espo_post(self, path, payload):
        self.posted.append((path, payload))
        if path == "/Lead/action/getConvertAttributes":
            return {}
        return self.convert_result

    async def add_lead_note(self, lead_id, note, **_kw):
        self.notes.append((lead_id, note))
        return True


def _wire(monkeypatch, fake: _FakeEspo):
    async def _noop_slack(_stats):
        return None

    monkeypatch.setattr(crm_lifecycle.espo, "list_all", fake.list_all)
    monkeypatch.setattr(crm_lifecycle.espo, "espo_post", fake.espo_post)
    monkeypatch.setattr(crm_lifecycle.espo, "add_lead_note", fake.add_lead_note)
    monkeypatch.setattr(crm_lifecycle, "_slack_report", _noop_slack)


@pytest.mark.asyncio
async def test_sweep_scores_and_converts_hot(monkeypatch):
    hot = _lead(
        id_="hot",
        account="Acme SA",
        website="acme.gr",
        description="migrate hosting, need a proposal, budget set " * 4
        + "\nutm_medium: cpc",
    )
    cold = _lead(id_="cold", email="x@gmail.com")
    fake = _FakeEspo([hot, cold])
    _wire(monkeypatch, fake)
    stats = await crm_lifecycle.run_crm_lifecycle_sweep()
    assert stats["scored"] == 2
    assert stats["converted"] == 1
    convert_calls = [p for p in fake.posted if p[0] == "/Lead/action/convert"]
    assert len(convert_calls) == 1
    records = convert_calls[0][1]["records"]
    assert "Contact" in records and "Opportunity" in records and "Account" in records


@pytest.mark.asyncio
async def test_sweep_never_converts_synthetic(monkeypatch):
    hot_messenger = _lead(
        id_="m",
        email="psid.9@messenger.local",
        account="FB User",
        website="fb.com",
        description="migrate launch budget proposal " * 4 + "\nutm_medium: cpc",
    )
    fake = _FakeEspo([hot_messenger])
    _wire(monkeypatch, fake)
    stats = await crm_lifecycle.run_crm_lifecycle_sweep()
    assert stats["converted"] == 0
    assert not any(p[0] == "/Lead/action/convert" for p in fake.posted)


@pytest.mark.asyncio
async def test_already_scored_not_rescored(monkeypatch):
    lead = _lead(id_="l1")
    fake = _FakeEspo([lead], notes_by_lead={"l1": [_note("Lead score: 30/100 (cold)")]})
    _wire(monkeypatch, fake)
    stats = await crm_lifecycle.run_crm_lifecycle_sweep()
    assert stats["scored"] == 0 and stats["rescored"] == 0
    assert fake.notes == []


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch):
    hot = _lead(
        id_="hot",
        account="Acme SA",
        website="acme.gr",
        description="migrate launch budget proposal timeline " * 4 + "\nutm_medium: cpc",
    )
    fake = _FakeEspo([hot])
    _wire(monkeypatch, fake)
    stats = await crm_lifecycle.run_crm_lifecycle_sweep(dry_run=True)
    assert stats["dry_run"] is True
    assert fake.notes == []
    assert not any(p[0] == "/Lead/action/convert" for p in fake.posted)
