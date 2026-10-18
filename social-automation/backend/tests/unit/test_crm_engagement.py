"""CRM engagement — IMAP reply → CRM note + status-bump write-back.

Covers ``app.services.crm_engagement`` and its Celery task wrapper. Fakes sit
only at the real external boundaries: dovecot IMAP, the EspoCRM HTTP client,
and the Slack transport — everything else runs for real.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.crm_engagement as E
import app.services.slack_notifications as SN
from app.worker.tasks import crm_engagement as task


def _settings(**over):
    base = dict(
        PROMO_SCAN_IMAP_HOST="",
        PROMO_SCAN_IMAP_PORT=993,
        PROMO_SCAN_MAILBOX="",
        PROMO_SCAN_IMAP_PASS="",
        SMTP_HOST="imap.test",
        SMTP_USER="ops@test.gr",
        SMTP_PASSWORD="pw",
        SMTP_SSL_VERIFY=False,
        SMTP_FROM="ops@cloudless.gr",
        CRM_ENGAGEMENT_ENABLED=True,
        CRM_ENGAGEMENT_LOOKBACK_DAYS=7,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _hdr(
    frm: str = "Customer <c@acme.gr>",
    subj: str = "Re: pricing question",
    mid: str = "<m1@acme.gr>",
    irt: str | None = None,
) -> bytes:
    lines = [f"From: {frm}", f"Subject: {subj}", f"Message-ID: {mid}"]
    if irt:
        lines.append(f"In-Reply-To: {irt}")
    return ("\r\n".join(lines) + "\r\n").encode()


class _FakeIMAP:
    """Drives _fetch_candidates: canned header fetches per message-id."""

    def __init__(self, messages: dict[bytes, bytes], search_ok: bool = True):
        self.messages = messages
        self.search_ok = search_ok

    def __call__(self, host, port, ssl_context=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.auth = (user, password)

    def select(self, mbox, readonly=False):
        self.mbox = mbox

    def search(self, charset, criterion, value):
        if not self.search_ok:
            return "NO", [None]
        return "OK", [b" ".join(self.messages)]

    def fetch(self, msg_id, spec):
        msg = self.messages.get(msg_id)
        if msg is None:
            return "NO", [None]
        return "OK", [(msg_id, msg)]


def _lead(id_: str = "l1", email: str = "c@acme.gr", status: str = "New"):
    return {"id": id_, "name": "Lead", "emailAddress": email, "status": status}


class _FakeEspo:
    def __init__(self, records=None, notes=None):
        self.records = records or {"Contact": [], "Lead": []}
        self.notes = notes or []
        self.note_calls: list[tuple[str, str, str]] = []
        self.patch_calls: list[tuple[str, dict]] = []
        self.fail_notes = False

    async def list_all(self, entity, **kw):
        if entity == "Note":
            return self.notes
        return self.records.get(entity, [])

    async def add_lead_note(self, rec_id, note, *, entity="Lead", **kw):
        if self.fail_notes:
            raise RuntimeError("espo down")
        self.note_calls.append((rec_id, entity, note))
        return True

    async def espo_patch(self, path, payload):
        self.patch_calls.append((path, payload))
        return {}


def _wire(monkeypatch, fake: _FakeEspo, slack: AsyncMock | None = None):
    slack = slack or AsyncMock()
    monkeypatch.setattr(E.espo, "list_all", fake.list_all)
    monkeypatch.setattr(E.espo, "add_lead_note", fake.add_lead_note)
    monkeypatch.setattr(E.espo, "espo_patch", fake.espo_patch)
    monkeypatch.setattr(SN, "post_digest_text_to_slack", slack)
    monkeypatch.setattr(E, "get_settings", lambda: _settings())
    return slack


def _wire_imap(monkeypatch, messages, search_ok=True):
    fake = _FakeIMAP(messages, search_ok=search_ok)
    monkeypatch.setattr(E.imaplib, "IMAP4_SSL", fake)
    monkeypatch.setattr(E, "get_settings", lambda: _settings())
    return fake


# ── pure helpers ────────────────────────────────────────────────────────────


def test_decode_rfc2047_and_plain():
    assert E._decode("Re: hello") == "Re: hello"
    assert E._decode("=?utf-8?q?caf=C3=A9?=") == "café"
    assert E._decode("=?utf-8?b?aGVsbG8=?=") == "hello"


def test_from_address_normalizes():
    assert E._from_address("Jane Doe <JANE@Acme.GR>") == "jane@acme.gr"
    assert E._from_address("plain@test.io") == "plain@test.io"
    assert E._from_address("") == ""


# ── _fetch_candidates ────────────────────────────────────────────────────────


def test_fetch_candidates_no_credentials(monkeypatch):
    monkeypatch.setattr(E, "get_settings", lambda: _settings(SMTP_PASSWORD="", SMTP_HOST=""))
    cands, errors = E._fetch_candidates(7)
    assert cands == []
    assert errors and "credentials" in errors[0]


def test_fetch_candidates_empty_search(monkeypatch):
    _wire_imap(monkeypatch, {})
    cands, errors = E._fetch_candidates(7)
    assert cands == [] and errors == []


def test_fetch_candidates_bad_search_status(monkeypatch):
    _wire_imap(monkeypatch, {b"1": _hdr()}, search_ok=False)
    cands, errors = E._fetch_candidates(7)
    assert cands == [] and errors == []


def test_fetch_candidates_parses_headers(monkeypatch):
    _wire_imap(
        monkeypatch,
        {
            b"1": _hdr(frm="Client <CLIENT@acme.gr>", subj="Re: hi", mid="<a@x>", irt="<orig@x>"),
            b"2": _hdr(frm="other@b.gr", subj="=?utf-8?q?caf=C3=A9?=", mid="<b@x>"),
        },
    )
    cands, errors = E._fetch_candidates(7)
    assert errors == [] and len(cands) == 2
    first, second = cands
    assert first["from"] == "client@acme.gr"
    assert first["message_id"] == "<a@x>"
    assert first["in_reply_to"] is True
    assert first["uid"] == "1"
    assert second["subject"] == "café"
    assert second["in_reply_to"] is False


def test_fetch_candidates_skips_failed_fetch(monkeypatch):
    _wire_imap(monkeypatch, {b"1": _hdr(), b"99": _hdr()}, search_ok=True)
    # 99 is not in the map → fetch returns NO and is skipped… but search lists keys.
    # Instead remove it via a fake whose search advertises an extra id:
    class _Extra(_FakeIMAP):
        def search(self, charset, criterion, value):
            return "OK", [b"1 99"]

    monkeypatch.setattr(E.imaplib, "IMAP4_SSL", _Extra({b"1": _hdr()}))
    cands, errors = E._fetch_candidates(7)
    assert errors == [] and len(cands) == 1


def test_fetch_candidates_imap_failure_reports(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(E.imaplib, "IMAP4_SSL", _boom)
    monkeypatch.setattr(E, "get_settings", lambda: _settings())
    cands, errors = E._fetch_candidates(7)
    assert cands == []
    assert errors and "mailbox scan" in errors[0]


# ── _crm_emails / _already_noted ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crm_emails_lead_wins_and_dead_skipped(monkeypatch):
    records = {
        "Contact": [
            {"id": "c1", "emailAddress": "dup@x.gr", "status": "New"},
            {"id": "c2", "emailAddress": "", "status": "New"},
            {"id": "c3", "emailAddress": "dead@x.gr", "status": "Dead"},
        ],
        "Lead": [
            {"id": "l1", "emailAddress": "dup@x.gr", "status": "New"},
            {"id": "l2", "emailAddress": "gone@x.gr", "status": "Converted"},
            {"id": "l3", "emailAddress": "live@x.gr", "status": "Assigned"},
        ],
    }
    async def _la(entity, **kw):
        return records.get(entity, [])

    monkeypatch.setattr(E.espo, "list_all", _la)
    profiles = await E._crm_emails()
    assert profiles["dup@x.gr"]["entity"] == "Lead"
    assert profiles["live@x.gr"]["id"] == "l3"
    assert "" not in profiles and "dead@x.gr" not in profiles and "gone@x.gr" not in profiles


@pytest.mark.asyncio
async def test_already_noted_marker_matching(monkeypatch):
    notes = [{"id": "n1", "post": "Reply received: hi · msg <m1@x>"}]

    async def _la(entity, **kw):
        return notes

    monkeypatch.setattr(E.espo, "list_all", _la)
    rec = {"id": "l1", "entity": "Lead"}
    assert await E._already_noted(rec, "<m1@x>") is True
    assert await E._already_noted(rec, "<new@x>") is False


# ── the sweep ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_disabled_gate(monkeypatch):
    monkeypatch.setattr(E, "get_settings", lambda: _settings(CRM_ENGAGEMENT_ENABLED=False))
    assert await E.run_crm_engagement_sweep() == {"enabled": False}


@pytest.mark.asyncio
async def test_sweep_notes_and_bumps_active_lead(monkeypatch):
    _wire_imap(
        monkeypatch,
        {
            b"1": _hdr(frm="Client <c@acme.gr>", subj="Re: audit", mid="<m1@acme>"),
            b"2": _hdr(frm="Us <ops@cloudless.gr>", subj="sent drip", mid="<m2@x>"),
            b"3": _hdr(frm="Stranger <s@nowhere.io>", subj="hi", mid="<m3@x>"),
        },
    )
    fake = _FakeEspo(records={"Contact": [], "Lead": [_lead(status="New")]})
    slack = _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep()

    assert stats["scanned"] == 3
    assert stats["matched"] == 1
    assert stats["noted"] == 1
    assert stats["status_bumped"] == 1
    assert fake.note_calls[0][0] == "l1" and "Reply received" in fake.note_calls[0][2]
    assert fake.patch_calls == [("/Lead/l1", {"status": "In Process"})]
    assert stats["details"][0]["status"] == "In Process"
    slack.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_dedupes_via_message_id(monkeypatch):
    _wire_imap(monkeypatch, {b"1": _hdr(mid="<m1@acme>")})
    fake = _FakeEspo(
        records={"Contact": [], "Lead": [_lead()]},
        notes=[{"id": "n1", "post": "Reply received: x · msg <m1@acme>"}],
    )
    _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep()
    assert stats["matched"] == 1 and stats["noted"] == 0
    assert fake.note_calls == [] and fake.patch_calls == []


@pytest.mark.asyncio
async def test_sweep_contact_match_no_status_bump(monkeypatch):
    _wire_imap(monkeypatch, {b"1": _hdr()})
    fake = _FakeEspo(records={"Contact": [_lead(id_="c1")], "Lead": []})
    _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep()
    assert stats["noted"] == 1 and stats["status_bumped"] == 0
    assert fake.patch_calls == []
    assert stats["details"][0]["entity"] == "Contact"


@pytest.mark.asyncio
async def test_sweep_dry_run_writes_nothing(monkeypatch):
    _wire_imap(monkeypatch, {b"1": _hdr()})
    fake = _FakeEspo(records={"Contact": [], "Lead": [_lead()]})
    _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep(dry_run=True)
    assert stats["dry_run"] is True
    assert stats["matched"] == 1 and stats["noted"] == 0
    assert stats["details"][0]["email"] == "c@acme.gr"
    assert fake.note_calls == [] and fake.patch_calls == []


@pytest.mark.asyncio
async def test_sweep_record_failure_does_not_stall(monkeypatch):
    _wire_imap(
        monkeypatch,
        {
            b"1": _hdr(frm="A <c@acme.gr>", mid="<m1@x>"),
            b"2": _hdr(frm="B <d@acme.gr>", mid="<m2@x>", subj="second"),
        },
    )
    fake = _FakeEspo(
        records={"Contact": [], "Lead": [_lead(id_="l1"), _lead(id_="l2", email="d@acme.gr")]},
    )
    fake.fail_notes = True
    _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep()
    assert stats["matched"] == 2 and stats["noted"] == 0
    assert stats["errors"] == 2


@pytest.mark.asyncio
async def test_sweep_scan_errors_surface_in_stats_and_slack(monkeypatch):
    monkeypatch.setattr(E.imaplib, "IMAP4_SSL", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    fake = _FakeEspo()
    slack = _wire(monkeypatch, fake)
    stats = await E.run_crm_engagement_sweep()
    assert stats["scanned"] == 0 and stats["errors"] == 1
    assert stats["scan_errors"]
    slack.assert_awaited_once()


# ── _slack_report ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_report_silent_when_nothing_happened(monkeypatch):
    slack = AsyncMock()
    monkeypatch.setattr(SN, "post_digest_text_to_slack", slack)
    await E._slack_report({"details": [], "errors": 0})
    slack.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_report_send_failure_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(
        SN, "post_digest_text_to_slack", AsyncMock(side_effect=RuntimeError("slack down"))
    )
    await E._slack_report(
        {"matched": 1, "noted": 1, "status_bumped": 0, "errors": 0, "dry_run": False, "details": [{"email": "a@b", "subject": "s"}]}
    )  # no exception escapes


# ── celery task wrapper ──────────────────────────────────────────────────────


def test_task_returns_service_stats(monkeypatch):
    async def _fake(*, dry_run: bool = False):
        return {"matched": 3, "dry_run": dry_run}

    monkeypatch.setattr(task.crm_engagement, "run_crm_engagement_sweep", _fake)
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    assert task.run_crm_engagement_sweep(dry_run=True) == {"matched": 3, "dry_run": True}


def test_task_failure_returns_none(monkeypatch):
    async def _boom(*, dry_run: bool = False):
        raise RuntimeError("imap gone")

    monkeypatch.setattr(task.crm_engagement, "run_crm_engagement_sweep", _boom)
    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.run(coro))
    assert task.run_crm_engagement_sweep() is None
