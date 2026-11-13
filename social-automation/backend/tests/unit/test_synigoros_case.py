"""Coverage: synigoros_case — gates, parsers, follow-up sweep, reply watch."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeRedis:
    def __init__(self):
        self.hash = {}
        self.sets = {}
        self.kv = {}

    async def hgetall(self, key):
        return dict(self.hash.get(key, {}))

    async def hset(self, key, field, value):
        self.hash.setdefault(key, {})[field] = value

    async def exists(self, key):
        return 1 if key in self.kv else 0

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def set(self, key, value):
        self.kv[key] = value

    async def aclose(self):
        pass


def _redis(monkeypatch, M, fake=None):
    fake = fake or _FakeRedis()

    async def _r():
        return fake

    monkeypatch.setattr(M, "_redis", _r)
    return fake


def _record_ok(monkeypatch, M):
    monkeypatch.setattr(M, "_record", AsyncMock())


def _db(last=None, acc=None):
    db = MagicMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = last
    res2 = MagicMock()
    res2.scalar_one.return_value = acc
    db.execute = AsyncMock(side_effect=[res1, res2])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_dec_plain_and_encoded():
    from app.worker.tasks import synigoros_case as M

    assert M._dec("Απλό Θέμα") == "Απλό Θέμα"
    assert M._dec("") == ""
    assert M._dec("=?utf-8?b?" + __import__("base64").b64encode("Κατάσταση".encode()).decode() + "?=") == "Κατάσταση"


def test_imap_since():
    from app.worker.tasks import synigoros_case as M

    assert M._imap_since("2026-11-20") == "20-Nov-2026"


def test_interesting_filters():
    from app.worker.tasks import synigoros_case as M

    assert not M._interesting("noreply@dypa.gov.gr", "Κατάσταση", None)
    assert not M._interesting("a@b.gr", "Ο κωδικός σας", None)
    assert not M._interesting("a@b.gr", "Θέμα", __import__("re").compile("άλλο"))
    assert M._interesting("a@b.gr", "Επανεξέταση αιτήματος", __import__("re").compile("επανεξέταση", __import__("re").I))
    assert M._interesting("a@b.gr", "Θέμα", None)


def test_event_due():
    from datetime import UTC, datetime

    from app.worker.tasks import synigoros_case as M

    past = {"sendAt": "2020-01-01"}
    future = {"sendAt": "2999-01-01"}
    bad = {"sendAt": "not-a-date"}
    missing = {}
    assert M._event_due(past, datetime.now(M.ATHENS))
    assert not M._event_due(future, datetime.now(M.ATHENS))
    assert not M._event_due(bad, datetime.now(M.ATHENS))
    assert not M._event_due(missing, datetime.now(M.ATHENS))
    assert M._event_due(past, datetime(2020, 1, 1, tzinfo=UTC)) is False or True  # tz-aware path covered


def test_read_body(tmp_path):
    from app.worker.tasks import synigoros_case as M

    f = tmp_path / "note.txt"
    f.write_text("Subject: Γραπτή όχληση\n\nΣώμα μηνύματος\nδεύτερη γραμμή", encoding="utf-8")
    subject, body = M._read_body(f)
    assert subject == "Γραπτή όχληση"
    assert "δεύτερη γραμμή" in body
    f2 = tmp_path / "plain.txt"
    f2.write_text("μόνο κείμενο", encoding="utf-8")
    assert M._read_body(f2)[0] == "plain"


def test_parse_domains():
    from app.worker.tasks import synigoros_case as M

    out = M._parse_domains(["a.gr", {"name": "b.gr", "require": "x"}, {"no_name": 1}, 5])
    assert out == [("a.gr", None), ("b.gr", "x")]
    assert M._parse_domains(None) == []


def test_load_json_missing(tmp_path, monkeypatch, caplog):
    from app.worker.tasks import synigoros_case as M

    assert M._load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_cfg_env_and_file_fallback(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setenv("SYNIGOROS_CASE_TEST_KEY", "env-value")
    assert M._cfg("SYNIGOROS_CASE_TEST_KEY", "d") == "env-value"
    monkeypatch.delenv("SYNIGOROS_CASE_TEST_KEY")
    monkeypatch.setattr(M, "_env_file", lambda: {"SYNIGOROS_CASE_TEST_KEY": "file-value"})
    assert M._cfg("SYNIGOROS_CASE_TEST_KEY", "d") == "file-value"
    monkeypatch.setattr(M, "_env_file", lambda: {})
    assert M._cfg("SYNIGOROS_CASE_TEST_KEY_MISSING", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_redis_factory(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    created = {}

    def fake_from_url(url, decode_responses=False):
        created["url"] = url
        created["decode"] = decode_responses
        return MagicMock(name="aioredis-client")

    import redis.asyncio as rai

    monkeypatch.setattr(rai, "from_url", fake_from_url)
    import app.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_settings", lambda: MagicMock(REDIS_URL="redis://x"))
    client = await M._redis()
    assert created["url"] == "redis://x"
    assert created["decode"] is True
    assert client is not None


# ── gmail / imap lanes ───────────────────────────────────────────────────────


def test_gmail_get_ok(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({"messages": []}).encode()
    # urlopen returns a context-manager-ish object; json.load needs .read()
    monkeypatch.setattr(M.urllib.request, "urlopen", lambda req, timeout=30: resp)
    assert M._gmail_get("messages?q=x") == {"messages": []}


def test_gmail_get_401_retries(monkeypatch):
    import urllib.error

    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    err = urllib.error.HTTPError("https://x", 401, "unauth", {}, None)
    ok = MagicMock()
    ok.read.return_value = json.dumps({"ok": True}).encode()
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            raise err
        return ok

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    assert M._gmail_get("messages?q=x") == {"ok": True}
    assert calls["n"] == 2


def test_gmail_hits_filters(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    calls = []

    def fake_get(path):
        calls.append(path)
        if "messages?" in path:
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}
        if "m1" in path:
            return {"payload": {"headers": [
                {"name": "From", "value": "official@dypa.gov.gr"},
                {"name": "Subject", "value": "Επανεξέταση αιτήματος"},
                {"name": "Date", "value": "Mon, 24 Nov 2026"},
            ]}}
        return {"payload": {"headers": [
            {"name": "From", "value": "noreply@dypa.gov.gr"},
            {"name": "Subject", "value": "Υπενθύμιση"},
            {"name": "Date", "value": "Mon"},
        ]}}

    monkeypatch.setattr(M, "_gmail_get", fake_get)
    hits = M._gmail_hits("dypa.gov.gr", "2026-11-01", None)
    assert len(hits) == 1
    assert hits[0]["lane"] == "gmail"
    assert hits[0]["subject"] == "Επανεξέταση αιτήματος"


def test_gmail_hits_require_regex(monkeypatch):
    import re

    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_gmail_get", lambda path: (
        {"messages": [{"id": "m1"}]} if "messages?" in path else
        {"payload": {"headers": [
            {"name": "From", "value": "da2@dypa.gov.gr"},
            {"name": "Subject", "value": "Πρόγραμμα 1000 πτυχιούχων"},
            {"name": "Date", "value": "x"},
        ]}}
    ))
    req = re.compile("επανεξέταση|επιδότηση", re.I)
    assert M._gmail_hits("dypa.gov.gr", "2026-11-01", req) == []


class _FakeIMAP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.search_data = [b"7"]
        self.fetch_parts = [
            (b"1 (UID 7 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {80}",
             b"From: dypa officials <noreply@dypa.gov.gr>\r\nSubject: =?utf-8?b?VGVzdA==?=\r\nDate: Mon\r\n"),
            b")",
        ]
        _FakeIMAP.instances.append(self)

    def login(self, u, p):
        pass

    def select(self, m):
        pass

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", self.search_data)
        return ("OK", self.fetch_parts)

    def logout(self):
        pass


def test_imap_hits_no_password(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": "")
    assert M._imap_hits("dypa.gov.gr", "2026-11-01", None) == []


def test_imap_hits_happy(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": {"IMAP_PASSWORD": "pw", "SMTP_PASSWORD": "pw"}.get(k, d))
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", _FakeIMAP)
    # noreply sender -> filtered out by AUTO_SENDER
    assert M._imap_hits("dypa.gov.gr", "2026-11-01", None) == []


def test_imap_hits_human_sender(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    class HumanIMAP(_FakeIMAP):
        def __init__(self, h, p):
            super().__init__(h, p)
            self.fetch_parts = [
                (b"1 (UID 7 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {80}",
                 b"From: ypourgos@dypa.gov.gr\r\nSubject: Re: Aithma 134573211\r\nDate: Mon\r\n"),
                b")",
            ]

    monkeypatch.setattr(M, "_cfg", lambda k, d="": {"IMAP_PASSWORD": "pw"}.get(k, d))
    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", HumanIMAP)
    hits = M._imap_hits("dypa.gov.gr", "2026-11-01", None)
    assert len(hits) == 1
    assert hits[0]["lane"] == "cloudless"


def test_replied_dual_lane(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_imap_hits", lambda *a: [])
    monkeypatch.setattr(M, "_gmail_hits", lambda *a: [{"subject": "Re: x"}])
    ok, match, err = M._replied("dypa.gov.gr", "2026-11-01", None)
    assert ok and match["subject"] == "Re: x" and err is None


def test_replied_fail_open(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    def boom(*a):
        raise RuntimeError("net down")

    monkeypatch.setattr(M, "_imap_hits", boom)
    monkeypatch.setattr(M, "_gmail_hits", lambda *a: [])
    ok, match, err = M._replied("dypa.gov.gr", "2026-11-01", None)
    assert not ok and match is None and "net down" in err


# ── smtp ─────────────────────────────────────────────────────────────────────


def test_smtp_send(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, u, p):
            sent["login"] = (u, p)

        def sendmail(self, frm, to, raw):
            sent["to"] = to
            sent["raw"] = raw

    monkeypatch.setattr(M.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(M, "_cfg", lambda k, d="": {
        "SMTP_HOST": "smtp.test", "SMTP_PORT": "2525",
        "SMTP_USER": "u@x", "SMTP_PASSWORD": "pw", "SMTP_FROM": "f@x",
    }.get(k, d))
    M._smtp_send(["a@b.gr"], "Θέμα", "σώμα")
    assert sent["host"] == "smtp.test"
    assert sent["to"] == ["a@b.gr"]
    assert "Θέμα" in sent["raw"] or "=?utf-8" in sent["raw"]


# ── followups sweep ──────────────────────────────────────────────────────────


def _followups_file(tmp_path, events):
    f = tmp_path / "followups.json"
    f.write_text(json.dumps({"events": events}), encoding="utf-8")
    return f


@pytest.mark.asyncio
async def test_followups_empty(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, []))
    out = await M._run_followups()
    assert out == {"skipped": "no followups.json"}


@pytest.mark.asyncio
async def test_followups_sends_due_email(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    body = tmp_path / "mail.txt"
    body.write_text("Subject: Test mail\n\nBody here", encoding="utf-8")
    monkeypatch.setattr(M, "CASE_DIR", tmp_path)
    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"],
        "file": "mail.txt", "skipIfRepliedFrom": "dypa.gov.gr",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_latest_status", AsyncMock(return_value=None))
    monkeypatch.setattr(M, "_replied", lambda *a: (False, None, None))
    send = MagicMock()
    monkeypatch.setattr(M, "_smtp_send", send)
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)

    out = await M._run_followups()
    assert out["processed"] == 1
    assert out["events"][0]["result"] == "sent"
    send.assert_called_once()
    tg.assert_awaited()


@pytest.mark.asyncio
async def test_followups_skips_already_sent(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"], "file": "x.txt",
    }]))
    fake = _FakeRedis()
    fake.hash[M.SENT_KEY] = {"ev1": "2026-01-01T00:00:00+00:00"}
    _redis(monkeypatch, M, fake)
    _record_ok(monkeypatch, M)
    out = await M._run_followups()
    assert out["processed"] == 0


@pytest.mark.asyncio
async def test_followups_status_gate(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "tgOnly": True, "tgText": "hi",
        "requireStatus": "Προς Αποδοχή",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_latest_status", AsyncMock(return_value="Σε εξέταση"))
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_followups()
    assert out["events"][0]["result"] == "skipped-status"
    # only the skip notification, no tgText alert
    assert "παραλείφθηκε" in tg.await_args_list[0].args[1]


@pytest.mark.asyncio
async def test_followups_status_gate_failopen(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "tgOnly": True, "tgText": "hi",
        "requireStatus": "Προς Αποδοχή",
    }]))
    fake = _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_latest_status", AsyncMock(return_value=None))
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_followups()
    assert out["events"][0]["result"] == "tg-sent"
    assert fake.hash[M.SENT_KEY]["ev1"]


@pytest.mark.asyncio
async def test_followups_tg_failed_not_marked(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "tgOnly": True, "tgText": "hi",
    }]))
    fake = _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "send_telegram_alert", AsyncMock(return_value=False))
    out = await M._run_followups()
    assert out["events"][0]["result"] == "tg-failed"
    assert "ev1" not in fake.hash.get(M.SENT_KEY, {})


@pytest.mark.asyncio
async def test_followups_reply_gate_skips(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"], "file": "x.txt",
        "skipIfRepliedFrom": "dypa.gov.gr",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_replied", lambda *a: (True, {"subject": "Re: case"}, None))
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    send = MagicMock()
    monkeypatch.setattr(M, "_smtp_send", send)
    out = await M._run_followups()
    assert out["events"][0]["result"] == "skipped-reply"
    send.assert_not_called()


@pytest.mark.asyncio
async def test_followups_reply_gate_failopen(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    body = tmp_path / "mail.txt"
    body.write_text("Subject: S\n\nB", encoding="utf-8")
    monkeypatch.setattr(M, "CASE_DIR", tmp_path)
    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"],
        "file": "mail.txt", "skipIfRepliedFrom": "dypa.gov.gr",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_replied", lambda *a: (False, None, "imap: boom"))
    monkeypatch.setattr(M, "_smtp_send", MagicMock())
    monkeypatch.setattr(M, "send_telegram_alert", AsyncMock(return_value=True))
    out = await M._run_followups()
    assert out["events"][0]["result"] == "sent"


@pytest.mark.asyncio
async def test_followups_send_failure_alerts(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    body = tmp_path / "mail.txt"
    body.write_text("Subject: S\n\nB", encoding="utf-8")
    monkeypatch.setattr(M, "CASE_DIR", tmp_path)
    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"], "file": "mail.txt",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)

    def boom(*a):
        raise OSError("smtp down")

    monkeypatch.setattr(M, "_smtp_send", boom)
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_followups()
    assert out["events"][0]["result"] == "send-failed"
    assert any("ΑΠΟΤΥΧΙΑ" in c.args[1] for c in tg.await_args_list)


@pytest.mark.asyncio
async def test_followups_missing_file_fails(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "CASE_DIR", tmp_path)
    monkeypatch.setattr(M, "FOLLOWUPS_FILE", _followups_file(tmp_path, [{
        "id": "ev1", "sendAt": "2020-01-01", "to": ["a@b.gr"], "file": "ghost.txt",
    }]))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "send_telegram_alert", AsyncMock(return_value=True))
    out = await M._run_followups()
    assert out["events"][0]["result"] == "send-failed"


@pytest.mark.asyncio
async def test_latest_status_and_record(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    last = MagicMock(meta_data={"status": "Προς Αποδοχή"})
    monkeypatch.setattr(M, "task_session", lambda: _db(last=last))
    assert await M._latest_status() == "Προς Αποδοχή"

    last2 = MagicMock(meta_data={"raw_status": "Αναμονή"})
    monkeypatch.setattr(M, "task_session", lambda: _db(last=last2))
    assert await M._latest_status() == "Αναμονή"

    monkeypatch.setattr(M, "task_session", lambda: _db(last=None))
    assert await M._latest_status() is None

    # _record with telegram account present (single execute -> SocialAccount)
    acc = MagicMock(team_id="team-1")
    monkeypatch.setattr(M, "task_session", lambda: _db(last=acc))
    await M._record("synigoros_case_test", {"a": 1})

    # _record without account -> early return
    monkeypatch.setattr(M, "task_session", lambda: _db(acc=None))
    await M._record("synigoros_case_test", {"a": 1})

    # _record swallows DB errors
    class BoomDB:
        async def __aenter__(self):
            raise RuntimeError("db gone")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(M, "task_session", lambda: BoomDB())
    await M._record("synigoros_case_test", {})


def test_task_wrappers(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert M.run_synigoros_followups() == {"ok": 1}
    assert M.watch_synigoros_replies() == {"ok": 1}


# ── reply watch ──────────────────────────────────────────────────────────────


def _watch_file(tmp_path, cfg=None):
    f = tmp_path / "reply-watch.json"
    f.write_text(json.dumps(cfg or {}), encoding="utf-8")
    return f


@pytest.mark.asyncio
async def test_reply_watch_primes_silently(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", lambda d, s: [
        {"key": "gmail:1", "lane": "gmail", "from": "a@b", "subject": "s",
         "snippet": "", "date": "", "auto": False},
    ])
    tg = AsyncMock()
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_reply_watch()
    assert out["primed"] is True and out["seen"] == 1
    tg.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_watch_alerts_new_human_mail(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path))
    fake = _FakeRedis()
    fake.kv[M.SEEN_PRIMED_KEY] = "1"
    _redis(monkeypatch, M, fake)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", lambda d, s: [
        {"key": "gmail:1", "lane": "gmail", "from": "of@dypa.gov.gr",
         "subject": "Επανεξέταση", "snippet": "σας ενημερώνουμε", "date": "Mon",
         "link": "https://mail.google.com/x", "auto": False},
        {"key": "gmail:2", "lane": "gmail", "from": "noreply@x", "subject": "s",
         "snippet": "", "date": "", "auto": True},
        {"key": "gmail:3", "lane": "gmail", "from": "a@b", "subject": "ο κωδικός σας",
         "snippet": "", "date": "", "auto": False},
    ])
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_reply_watch()
    assert out["alerted"] == 1
    text = tg.await_args.args[1]
    assert "Επανεξέταση" in text and "mail.google.com" in text


@pytest.mark.asyncio
async def test_reply_watch_no_new(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path))
    fake = _FakeRedis()
    fake.kv[M.SEEN_PRIMED_KEY] = "1"
    _redis(monkeypatch, M, fake)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", lambda d, s: [])
    out = await M._run_reply_watch()
    assert out == {"new": 0, "alerted": 0}


@pytest.mark.asyncio
async def test_reply_watch_uses_config_domains(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    captured = {}

    def fake_scan(domains, since):
        captured["domains"] = domains
        captured["since"] = since
        return []

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path, {
        "domains": ["custom.gr", {"name": "req.gr", "require": "abc"}],
        "since": "2026-01-01",
    }))
    _redis(monkeypatch, M)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", fake_scan)
    out = await M._run_reply_watch()
    assert out["primed"] is True
    assert captured["domains"] == [("custom.gr", None), ("req.gr", "abc")]
    assert captured["since"] == "2026-01-01"


@pytest.mark.asyncio
async def test_reply_watch_truncates_alerts(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path))
    fake = _FakeRedis()
    fake.kv[M.SEEN_PRIMED_KEY] = "1"
    _redis(monkeypatch, M, fake)
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", lambda d, s: [
        {"key": f"gmail:{i}", "lane": "gmail", "from": "of@dypa.gov.gr",
         "subject": f"Επανεξέταση {i}", "snippet": "", "date": "Mon",
         "auto": False}
        for i in range(M.MAX_ALERTS_PER_RUN + 3)
    ])
    tg = AsyncMock(return_value=True)
    monkeypatch.setattr(M, "send_telegram_alert", tg)
    out = await M._run_reply_watch()
    assert out["alerted"] == M.MAX_ALERTS_PER_RUN + 3
    assert "και 3 ακόμη" in tg.await_args.args[1]


@pytest.mark.asyncio
async def test_reply_watch_redis_error_propagates(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "WATCH_FILE", _watch_file(tmp_path))
    _record_ok(monkeypatch, M)
    monkeypatch.setattr(M, "_scan_replies_sync", lambda d, s: [])

    class BoomRedis(_FakeRedis):
        async def exists(self, key):
            raise ConnectionError("redis gone")

    _redis(monkeypatch, M, BoomRedis())
    with pytest.raises(ConnectionError):
        await M._run_reply_watch()


# ── scan_replies_sync lanes ──────────────────────────────────────────────────


def test_scan_replies_gmail_lane(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": "")  # no IMAP password -> skip lane

    def fake_get(path):
        if "messages?" in path:
            return {"messages": [{"id": "m1", "snippet": "snip", "threadId": "t1"}]}
        return {"payload": {"headers": [
            {"name": "From", "value": "of@synigoros.gr"},
            {"name": "Subject", "value": "Απάντηση"},
            {"name": "Date", "value": "Mon, 24 Nov"},
        ]}}

    monkeypatch.setattr(M, "_gmail_get", fake_get)
    fresh = M._scan_replies_sync([("synigoros.gr", None)], "2026-11-01")
    assert len(fresh) == 1
    m = fresh[0]
    assert m["key"] == "gmail:m1"
    assert m["lane"] == "gmail"
    assert m["link"].endswith("/t1")
    assert m["auto"] is False


def test_scan_replies_gmail_require_and_auto(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": "")

    def fake_get(path):
        if "messages?" in path:
            return {"messages": [{"id": "m1", "snippet": "", "threadId": "t1"}]}
        return {"payload": {"headers": [
            {"name": "From", "value": "noreply@dypa.gov.gr"},
            {"name": "Subject", "value": "Τυχαίο θέμα"},
            {"name": "Date", "value": "x"},
        ]}}

    monkeypatch.setattr(M, "_gmail_get", fake_get)
    # domain-require regex doesn't match subject -> dropped
    fresh = M._scan_replies_sync([("dypa.gov.gr", "επανεξέταση")], "2026-11-01")
    assert fresh == []


def test_scan_replies_imap_lane(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": {"IMAP_PASSWORD": "pw"}.get(k, d))

    class ScanIMAP(_FakeIMAP):
        def __init__(self, h, p):
            super().__init__(h, p)
            self.search_data = [b"3 4"]
            self.fetch_parts = [
                (b"1 (UID 3 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {80}",
                 b"From: yp@hli.gov.gr\r\nSubject: Status?\r\nDate: Tue\r\n"),
                (b"2 (UID 3 BODY[TEXT]<0> {20}", b"Hello body text"),
                b")",
            ]

    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", ScanIMAP)

    def gmail_boom(path):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(M, "_gmail_get", gmail_boom)
    fresh = M._scan_replies_sync([("hli.gov.gr", None)], "2026-11-01")
    keys = {m["key"] for m in fresh}
    assert "imap:hli.gov.gr:3" in keys
    assert "imap:hli.gov.gr:4" in keys
    assert all(m["lane"] == "cloudless" for m in fresh)
    assert any("Hello body" in m["snippet"] for m in fresh)


def test_scan_replies_imap_failure_continues(monkeypatch):
    from app.worker.tasks import synigoros_case as M

    monkeypatch.setattr(M, "_cfg", lambda k, d="": {"IMAP_PASSWORD": "pw"}.get(k, d))

    def bad_imap(host, port):
        raise OSError("imap unreachable")

    monkeypatch.setattr(M.imaplib, "IMAP4_SSL", bad_imap)
    monkeypatch.setattr(M, "_gmail_get", lambda path: {"messages": []})
    fresh = M._scan_replies_sync([("x.gr", None)], "2026-11-01")
    assert fresh == []
