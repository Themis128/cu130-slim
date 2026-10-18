"""Coverage for app/services/lead_research.py — the Sales dept diligence loop."""
import pytest

import app.services.lead_research as R


def _lead(**kw):
    d = dict(
        id="lead-1", name="Acme Corp", emailAddress="info@acme.example",
        website="https://acme.example", accountName="Acme Corp", accountId="acc-1",
        industry="Software", title="CTO", source="Web Site", status="New",
        createdAt="2026-10-10T00:00:00Z",
    )
    d.update(kw)
    return d


# ── candidate selection ─────────────────────────────────────────────────────

def test_candidates_drop_dead_and_converted():
    leads = [_lead(status="New"), _lead(id="l2", status="Dead"), _lead(id="l3", status="Converted")]
    out = R._candidate_leads(leads)
    assert [lead["id"] for lead in out] == ["lead-1"]


def test_candidates_keep_leads_with_any_context():
    # no website — still has a name to research on Wikipedia
    assert len(R._candidate_leads([_lead(website=None)])) == 1
    # truly context-free lead (id only) is dropped
    assert len(R._candidate_leads([_lead(website=None, accountName=None, name=None)])) == 0


# ── public-url guard (SSRF) ──────────────────────────────────────────────────

def test_public_url_rejects_private_and_loopback():
    assert R._public_url("http://192.168.1.200/x") is None
    assert R._public_url("http://127.0.0.1:8080") is None
    assert R._public_url("http://10.0.0.5") is None
    assert R._public_url("javascript:alert(1)") is None


def test_public_url_accepts_real_host():
    assert R._public_url("example.com") == "https://example.com"
    assert R._public_url("https://example.com/a?b=1") == "https://example.com/a?b=1"


# ── note composition ────────────────────────────────────────────────────────

def test_compose_note_includes_context_site_and_wiki():
    note = R._compose_note(_lead(), "Acme — Cloud software for teams", "Acme Corp is a software firm.")
    assert note.startswith(R.RESEARCH_MARKER + " Acme Corp")
    assert "title=CTO" in note and "industry=Software" in note and "source=Web Site" in note
    assert "Website (https://acme.example): Acme — Cloud software" in note
    assert "Wikipedia: Acme Corp is a software firm." in note


def test_compose_note_none_when_no_signal():
    lead = _lead(title=None, industry=None, source=None)
    assert R._compose_note(lead, None, None) is None


def test_compose_note_without_research_signals_still_has_context():
    note = R._compose_note(_lead(website=None), None, None)
    assert note is not None and "CRM context" in note and "Website" not in note


# ── dedupe via note marker ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_researched_detects_marker(monkeypatch):
    async def fake_list_all(entity, **kw):
        return [{"id": "n1", "post": "Lead research — Acme\nWebsite: x"}]

    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    assert await R._already_researched("lead-1") is True


@pytest.mark.asyncio
async def test_not_researched_without_marker(monkeypatch):
    async def fake_list_all(entity, **kw):
        return [{"id": "n1", "post": "some other note"}]

    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    assert await R._already_researched("lead-1") is False


# ── sweep orchestration ─────────────────────────────────────────────────────

def _fake_client():
    """Async-context-manager stub standing in for httpx.AsyncClient."""
    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return _C()


@pytest.mark.asyncio
async def test_sweep_writes_note_once(monkeypatch):
    leads = [_lead()]
    calls = {"notes": [], "list_all": 0}

    async def fake_list_all(entity, **kw):
        calls["list_all"] += 1
        return leads if entity == "Lead" else []  # no existing notes

    async def fake_add(lid, note, *, entity):
        calls["notes"].append((lid, note))
        return True

    async def fake_site(url, client):
        return "Acme — cloud tools"

    async def fake_wiki(name, client):
        return "Acme Corp is a software firm."

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R.espo, "add_lead_note", fake_add)
    monkeypatch.setattr(R, "_fetch_site_summary", fake_site)
    monkeypatch.setattr(R, "_wikipedia_summary", fake_wiki)
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())

    stats = await R.run_lead_research()
    assert stats["researched"] == 1 and stats["skipped"] == 0 and stats["errors"] == 0
    assert calls["notes"] == [("lead-1", stats["notes"][0]["note"])]


@pytest.mark.asyncio
async def test_sweep_skips_already_researched(monkeypatch):
    async def fake_list_all(entity, **kw):
        if entity == "Note":
            return [{"id": "n1", "post": "Lead research — Acme\n…"}]
        return [_lead()]

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())

    stats = await R.run_lead_research()
    assert stats["researched"] == 0 and stats["skipped"] == 1


@pytest.mark.asyncio
async def test_sweep_dry_run_writes_nothing(monkeypatch):
    writes = []

    async def fake_list_all(entity, **kw):
        return [_lead()] if entity == "Lead" else []

    async def fake_add(lid, note, *, entity):
        writes.append(lid)
        return True

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R.espo, "add_lead_note", fake_add)
    monkeypatch.setattr(R, "_fetch_site_summary", lambda *a: _noop())
    monkeypatch.setattr(R, "_wikipedia_summary", lambda *a: _noop())
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())

    stats = await R.run_lead_research(dry_run=True)
    assert stats["dry_run"] is True and stats["researched"] == 1 and not writes


async def _noop():
    return None


@pytest.mark.asyncio
async def test_sweep_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(R.espo, "configured", lambda: False)
    assert (await R.run_lead_research())["status"] == "skipped"
