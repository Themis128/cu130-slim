"""Coverage for app/services/lead_research.py — the Sales dept diligence loop."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.lead_research as R


class _Resp:
    def __init__(self, status=200, headers=None, text="", json_data=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self._json = json_data

    def json(self):
        return self._json or {}


class _FakeHTTP:
    """AsyncClient stand-in serving canned responses keyed by URL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url, **kw):
        self.calls.append(url)
        resp = self.routes.get(url.split("?")[0].rstrip("/"))
        if isinstance(resp, Exception):
            raise resp
        return resp or _Resp(status=404)


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


# ── real fetch paths (httpx boundary faked only) ────────────────────────────

_HTML = (
    '<html><head><title>Acme Corp</title>'
    '<meta name="description" content="Cloud software for shipping teams"></head></html>'
)


@pytest.mark.asyncio
async def test_site_summary_extracts_title_and_meta():
    client = _FakeHTTP(
        {"https://93.184.216.34": _Resp(headers={"content-type": "text/html"}, text=_HTML)}
    )
    out = await R._fetch_site_summary("93.184.216.34", client)
    assert out == "Acme Corp — Cloud software for shipping teams"
    assert client.calls == ["https://93.184.216.34"]


@pytest.mark.asyncio
async def test_site_summary_alt_meta_attribute_order():
    html = '<meta content="Ship faster with us today" name="description"><title>Tiny</title>'
    client = _FakeHTTP(
        {"https://93.184.216.34": _Resp(headers={"content-type": "text/html"}, text=html)}
    )
    out = await R._fetch_site_summary("93.184.216.34", client)
    assert out == "Tiny — Ship faster with us today"


@pytest.mark.asyncio
async def test_site_summary_rejects_bad_status_and_non_html():
    client = _FakeHTTP(
        {"https://93.184.216.34": _Resp(status=404, headers={"content-type": "text/html"})}
    )
    assert await R._fetch_site_summary("93.184.216.34", client) is None
    client = _FakeHTTP(
        {"https://93.184.216.34": _Resp(headers={"content-type": "application/pdf"}, text="x")}
    )
    assert await R._fetch_site_summary("93.184.216.34", client) is None


@pytest.mark.asyncio
async def test_site_summary_private_url_and_fetch_error():
    assert await R._fetch_site_summary("http://10.0.0.9", _FakeHTTP({})) is None
    client = _FakeHTTP({"https://93.184.216.34": TimeoutError("slow")})
    assert await R._fetch_site_summary("93.184.216.34", client) is None


def test_public_url_dns_failure_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no dns")

    monkeypatch.setattr(R.socket, "getaddrinfo", _boom)
    assert R._public_url("unresolvable.invalid") is None


@pytest.mark.asyncio
async def test_wikipedia_summary_first_hit():
    data = {"pages": [{"title": "Acme", "excerpt": "<b>Acme</b> is a software firm."}]}
    client = _FakeHTTP(
        {"https://en.wikipedia.org/w/rest.php/v1/search/page": _Resp(json_data=data)}
    )
    assert await R._wikipedia_summary("Acme", client) == "Acme: Acme is a software firm."


@pytest.mark.asyncio
async def test_wikipedia_summary_empty_and_failures():
    url = "https://en.wikipedia.org/w/rest.php/v1/search/page"
    client = _FakeHTTP({url: _Resp(status=500)})
    assert await R._wikipedia_summary("Acme", client) is None
    client = _FakeHTTP({url: _Resp(json_data={"pages": []})})
    assert await R._wikipedia_summary("Acme", client) is None
    client = _FakeHTTP({url: TimeoutError()})
    assert await R._wikipedia_summary("Acme", client) is None


# ── sweep error / report paths ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_note_write_failure_counts_error(monkeypatch):
    async def fake_list_all(entity, **kw):
        return [_lead()] if entity == "Lead" else []

    async def fake_add(lid, note, *, entity):
        return False

    import app.services.slack_notifications as SN

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R.espo, "add_lead_note", fake_add)
    monkeypatch.setattr(R, "_fetch_site_summary", lambda *a: _noop())
    monkeypatch.setattr(R, "_wikipedia_summary", lambda *a: _noop())
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())
    monkeypatch.setattr(SN, "post_digest_text_to_slack", AsyncMock())

    stats = await R.run_lead_research()
    assert stats["errors"] == 1 and stats["researched"] == 0


@pytest.mark.asyncio
async def test_sweep_lead_exception_counts_error(monkeypatch):
    async def fake_list_all(entity, **kw):
        if entity == "Note":
            raise RuntimeError("espo down")
        return [_lead()]

    import app.services.slack_notifications as SN

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())
    monkeypatch.setattr(SN, "post_digest_text_to_slack", AsyncMock())

    stats = await R.run_lead_research()
    assert stats["errors"] == 1 and stats["researched"] == 0


@pytest.mark.asyncio
async def test_sweep_skips_lead_with_no_research_signal(monkeypatch):
    lead = _lead(title=None, industry=None, source=None)

    async def fake_list_all(entity, **kw):
        return [lead] if entity == "Lead" else []

    monkeypatch.setattr(R.espo, "configured", lambda: True)
    monkeypatch.setattr(R.espo, "list_all", fake_list_all)
    monkeypatch.setattr(R, "_fetch_site_summary", lambda *a: _noop())
    monkeypatch.setattr(R, "_wikipedia_summary", lambda *a: _noop())
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _fake_client())

    stats = await R.run_lead_research()
    assert stats["skipped"] == 1 and stats["researched"] == 0


@pytest.mark.asyncio
async def test_slack_report_failure_swallowed(monkeypatch):
    import app.services.slack_notifications as SN

    monkeypatch.setattr(
        SN, "post_digest_text_to_slack", AsyncMock(side_effect=RuntimeError("slack down"))
    )
    await R._slack_report(
        {
            "researched": 1,
            "skipped": 0,
            "errors": 0,
            "candidates": 1,
            "notes": [{"name": "n", "note": "Lead research — x"}],
        }
    )


# ── celery task wrapper ──────────────────────────────────────────────────────


def test_task_disabled_by_settings_gate(monkeypatch):
    from app.worker.tasks import lead_research as task

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(LEAD_RESEARCH_ENABLED=False),
    )
    called = []
    monkeypatch.setattr(task, "run_async", lambda coro: called.append(coro))
    assert task.run_lead_research_sweep() is None
    assert called == []


def test_task_returns_service_stats(monkeypatch):
    from app.worker.tasks import lead_research as task

    async def _fake(*, dry_run: bool = False):
        return {"researched": 2, "dry_run": dry_run}

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(LEAD_RESEARCH_ENABLED=True),
    )
    monkeypatch.setattr(task.lead_research, "run_lead_research", _fake)
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    assert task.run_lead_research_sweep(dry_run=True) == {"researched": 2, "dry_run": True}


def test_task_failure_returns_none(monkeypatch):
    from app.worker.tasks import lead_research as task

    async def _boom(*, dry_run: bool = False):
        raise RuntimeError("imap gone")

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(LEAD_RESEARCH_ENABLED=True),
    )
    monkeypatch.setattr(task.lead_research, "run_lead_research", _boom)
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    assert task.run_lead_research_sweep() is None
