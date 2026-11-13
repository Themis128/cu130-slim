"""Coverage: synigoros_case — follow-up sweep, reply watch, lane helpers."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mod():
    from app.worker.tasks import synigoros_case as M

    return M


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_cfg_env_wins(mod, monkeypatch):
    monkeypatch.setenv("SYNIGOROS_TEST_KEY", "from-env")
    assert mod._cfg("SYNIGOROS_TEST_KEY", "d") == "from-env"
    assert mod._cfg("SYNIGOROS_MISSING_KEY_XYZ", "d") == "d"


def test_load_json_missing_and_malformed(mod, tmp_path):
    assert mod._load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert mod._load_json(bad, [1]) == [1]
    good = tmp_path / "good.json"
    good.write_text('{"a": 2}', encoding="utf-8")
    assert mod._load_json(good, {}) == {"a": 2}


def test_dec_handles_encoded_and_plain(mod):
    assert mod._dec("") == ""
    assert mod._dec("=?utf-8?b?4oKs?=")  # smoke: no crash on encoded word
    assert mod._dec("plain subject") == "plain subject"


def test_imap_since_format(mod):
    assert mod._imap_since("2026-11-13") == "13-Nov-2026"
    assert mod._imap_since("2026-01-05") == "05-Jan-2026"


def test_interesting_filters(mod):
    req = mod.re.compile("προσφυγ", mod.re.I)
    assert mod._interesting("harvester@dypa.gov.gr", "Νέα για την προσφυγή σας", req)
    assert not mod._interesting("noreply@x.gr", "Κατάσταση υπόθεσης", None)
    assert not mod._interesting("a@b.gr", "Το OTP σας", None)
    assert not mod._interesting("a@b.gr", "Κατάσταση", req)  # require not met
    assert mod._interesting("a@b.gr", "Νέα απόφαση", None)


def test_parse_domains(mod):
    assert mod._parse_domains(None) == []
    assert mod._parse_domains(["a.gr", {"name": "b.gr", "require": "x"}, {"n": 1}, 5]) == [
        ("a.gr", None),
        ("b.gr", "x"),
    ]


def test_read_body(mod, tmp_path):
    p = tmp_path / "msg.txt"
    p.write_text("Subject: Test Θέμα\n\nBody line1\nline2\n", encoding="utf-8")
    subj, body = mod._read_body(p)
    assert subj == "Test Θέμα"
    assert "Body line1" in body
    p2 = tmp_path / "nosubj.txt"
    p2.write_text("just body", encoding="utf-8")
    assert mod._read_body(p2)[0] == "nosubj"


def test_event_due(mod):
    now = mod.datetime(2026, 11, 13, 9, 0, tzinfo=mod.ATHENS)
    assert mod._event_due({"sendAt": "2026-11-13"}, now)
    early = mod.datetime(2026, 11, 13, 7, 0, tzinfo=mod.ATHENS)
    assert not mod._event_due({"sendAt": "2026-11-13"}, early)
    assert not mod._event_due({}, now)
    assert not mod._event_due({"sendAt": "bogus"}, now)


# ── gmail_get ────────────────────────────────────────────────────────────────


def test_gmail_get_success(mod, monkeypatch):
    monkeypatch.setattr(mod, "_gmail_access_token", lambda: "tok")
    payload = {"messages": []}
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(payload))
    assert mod._gmail_get("messages?q=x") == payload


def test_gmail_get_401_retries(mod, monkeypatch):
    monkeypatch.setattr(mod, "_gmail_access_token", lambda: "tok")
    calls = []

    def fake_urlopen(req, timeout=30):
        calls.append(req)
        if len(calls) == 1:
            raise mod.urllib.error.HTTPError(req.full_url, 401, "unauth", {}, None)
        return _FakeResp({"ok": True})

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    assert mod._gmail_get("x") == {"ok": True}
    assert len(calls) == 2


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode()

    # urllib response used via json.load -> needs .read()
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── lane helpers ─────────────────────────────────────────────────────────────


def test_gmail_hits_filters(mod, monkeypatch):
    monkeypatch.setattr(
        mod, "_gmail_get",
        lambda path: {"messages": [{"id": "m1"}]} if "messages?q" in path else {
            "payload": {"headers": [
                {"name": "From", "value": "dypa@dypa.gov.gr"},
                {"name": "Subject", "value": "Νέα απόφαση για την προσφυγή σας"},
                {"name": "Date", "value": "Thu, 13 Nov 2026"},
            ]}
        },
    )
    hits = mod._gmail_hits("dypa.gov.gr", "2026-11-01", None)
    assert len(hits) == 1 and hits[0]["lane"] == "gmail"
    hits2 = mod._gmail_hits("dypa.gov.gr", "2026-11-01", mod.re.compile("απόρριψη", mod.re.I))
    assert hits2 == []


def test_imap_hits_no_password(mod, monkeypatch):
    monkeypatch.setattr(mod, "_cfg", lambda k, d="": "" if "PASSWORD" in k else d)
    assert mod._imap_hits("x.gr", "2026-01-01", None) == []


def test_replied_dual_lane(mod, monkeypatch):
    monkeypatch.setattr(mod, "_imap_hits", lambda *a: [{"subject": "re"}])
    monkeypatch.setattr(mod, "_gmail_hits", lambda *a: [])
    replied, match, err = mod._replied("d.gr", "2026-01-01", None)
    assert replied and match["subject"] == "re" and err is None

    monkeypatch.setattr(mod, "_imap_hits", lambda *a: (_ for _ in ()).throw(RuntimeError("imap down")))
    monkeypatch.setattr(mod, "_gmail_hits", lambda *a: [])
    replied, match, err = mod._replied("d.gr", "2026-01-01", None)
    assert not replied and "imap down" in err


# ── smtp + body ──────────────────────────────────────────────────────────────


def test_smtp_send(mod, monkeypatch):
    monkeypatch.setattr(mod, "_cfg", lambda k, d="": {"SMTP_USER": "u", "SMTP_PASSWORD": "p"}.get(k, d))
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(mod.smtplib, "SMTP", MagicMock(return_value=fake))
    mod._smtp_send(["a@b.gr"], "Θέμα", "σώμα")
    fake.login.assert_called_once_with("u", "p")
    fake.sendmail.assert_called_once()


# ── _record / _latest_status ─────────────────────────────────────────────────


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _wire_db(monkeypatch, mod, scalar=None, added=None):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock(side_effect=lambda o: added.append(o) if added is not None else None)
    db.commit = AsyncMock()
    monkeypatch.setattr(mod, "task_session", lambda: _FakeSession(db))
    return db


@pytest.mark.asyncio
async def test_record_adds_event(mod, monkeypatch):
    added = []
    acc = MagicMock(team_id="t1")
    _wire_db(monkeypatch, mod, scalar=acc, added=added)
    await mod._record("ev_type", {"k": 1})
    assert added and added[0].event_type == "ev_type" and added[0].platform == "synigoros"


@pytest.mark.asyncio
async def test_record_no_account_noop(mod, monkeypatch):
    added = []
    _wire_db(monkeypatch, mod, scalar=None, added=added)
    await mod._record("ev", {})
    assert added == []


@pytest.mark.asyncio
async def test_record_swallows_errors(mod, monkeypatch):
    class Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mod, "task_session", lambda: Boom())
    await mod._record("ev", {})  # must not raise


@pytest.mark.asyncio
async def test_latest_status(mod, monkeypatch):
    ev = MagicMock()
    ev.meta_data = {"status": "Σε εξέταση"}
    _wire_db(monkeypatch, mod, scalar=ev)
    assert await mod._latest_status() == "Σε εξέταση"
    ev.meta_data = {"raw_status": "raw-x"}
    assert await mod._latest_status() == "raw-x"
    _wire_db(monkeypatch, mod, scalar=None)
    assert await mod._latest_status() is None


# ── fake redis ───────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, sent=None, seen=None, primed=True):
        self.hash = dict(sent or {})
        self.seen = set(seen or [])
        self.primed = primed
        self.closed = False

    async def hgetall(self, k):
        return dict(self.hash)

    async def hset(self, k, f, v):
        self.hash[f] = v

    async def exists(self, k):
        return 1 if self.primed else 0

    async def sismember(self, k, m):
        return m in self.seen

    async def sadd(self, k, *members):
        self.seen.update(members)

    async def set(self, k, v):
        self.primed = True

    async def aclose(self):
        self.closed = True


def _patch_async_redis(mod, monkeypatch, fake):
    async def _fake_redis():
        return fake

    monkeypatch.setattr(mod, "_redis", _fake_redis)


# ── _run_followups ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followups_empty_schedule(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": []})
    out = await mod._run_followups()
    assert out == {"skipped": "no followups.json"}


@pytest.mark.asyncio
async def test_followups_sends_email(mod, monkeypatch):
    ev = {
        "id": "e1", "sendAt": "2026-11-01", "file": "x.txt",
        "to": ["a@b.gr"],
    }
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_read_body", lambda p: ("Θέμα", "σώμα"))
    sent = []
    monkeypatch.setattr(mod, "_smtp_send", lambda to, s, b: sent.append((to, s)))
    alerts = []
    monkeypatch.setattr(mod, "send_telegram_alert", lambda *a: alerts.append(a) or _async_true())
    monkeypatch.setattr(mod, "_record", AsyncMock())

    now = mod.datetime.now(mod.ATHENS)
    ev["sendAt"] = now.date().isoformat()
    out = await mod._run_followups()
    assert out["processed"] == 1 and out["events"][0]["result"] == "sent"
    assert sent == [(["a@b.gr"], "Θέμα")]
    assert fake.closed


async def _async_true():
    return True


@pytest.mark.asyncio
async def test_followups_already_sent_and_not_due(mod, monkeypatch):
    past = {"id": "old", "sendAt": "2020-01-01", "file": "x", "to": ["a"]}
    future = {"id": "new", "sendAt": "2999-01-01", "file": "x", "to": ["a"]}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [past, future]})
    fake = _FakeRedis(sent={"old": "2020-01-01T00:00:00+00:00"})
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["processed"] == 0


@pytest.mark.asyncio
async def test_followups_tg_only(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "tg1", "sendAt": now.date().isoformat(), "tgOnly": True, "tgText": "υπενθύμιση"}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)
    alerts = []

    async def _alert(*a):
        alerts.append(a)
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "tg-sent"
    assert "υπενθύμιση" in alerts[0][1]


@pytest.mark.asyncio
async def test_followups_status_gate_skip(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "sg", "sendAt": now.date().isoformat(), "file": "x", "to": ["a"],
          "requireStatus": "εκκρεμεί"}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)

    async def _status():
        return "ολοκληρώθηκε"

    monkeypatch.setattr(mod, "_latest_status", _status)

    async def _alert(*a):
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "skipped-status"


@pytest.mark.asyncio
async def test_followups_send_failure_alerts(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "bad", "sendAt": now.date().isoformat(), "file": "x", "to": ["a"]}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_read_body", lambda p: ("s", "b"))

    def _boom(*a):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mod, "_smtp_send", _boom)
    alerts = []

    async def _alert(*a):
        alerts.append(a)
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "send-failed"
    assert any("ΑΠΟΤΥΧΙΑ" in str(a) for a in alerts)


# ── _run_reply_watch ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_watch_primes_silently(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"domains": [{"name": "x.gr"}], "since": "2026-01-01"})
    monkeypatch.setattr(mod, "_scan_replies_sync", lambda d, s: [{"key": "k1", "lane": "gmail", "subject": "s", "from": "f", "date": "d"}])
    fake = _FakeRedis(primed=False)
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_reply_watch()
    assert out == {"primed": True, "seen": 1}


@pytest.mark.asyncio
async def test_reply_watch_alerts_new_interesting(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"domains": [{"name": "x.gr"}], "since": "2026-01-01"})
    hit = {"key": "new1", "lane": "gmail", "subject": "Νέα απάντηση", "from": "f@x.gr", "date": "d", "snippet": "snip"}
    monkeypatch.setattr(mod, "_scan_replies_sync", lambda d, s: [hit])
    fake = _FakeRedis(primed=True)
    _patch_async_redis(mod, monkeypatch, fake)
    alerts = []

    async def _alert(*a):
        alerts.append(a)
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_reply_watch()
    assert out["alerted"] == 1
    assert "ΝΕΑ ΑΛΛΗΛΟΓΡΑΦΙΑ" in alerts[0][1]


@pytest.mark.asyncio
async def test_reply_watch_skips_noise_and_dupes(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"domains": [{"name": "x.gr"}], "since": "2026-01-01"})
    hits = [
        {"key": "dup", "lane": "gmail", "subject": "s", "from": "f", "date": "d"},
        {"key": "noise", "lane": "gmail", "subject": "Το OTP σας", "from": "f", "date": "d", "auto": False},
        {"key": "auto", "lane": "gmail", "subject": "s", "from": "f", "date": "d", "auto": True},
    ]
    monkeypatch.setattr(mod, "_scan_replies_sync", lambda d, s: hits)
    fake = _FakeRedis(primed=True, seen={"dup"})
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_reply_watch()
    assert out == {"new": 2, "alerted": 0}


@pytest.mark.asyncio
async def test_reply_watch_default_domains(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {})
    captured = {}
    monkeypatch.setattr(mod, "_scan_replies_sync", lambda d, s: captured.update(doms=d, since=s) or [])
    fake = _FakeRedis(primed=True)
    _patch_async_redis(mod, monkeypatch, fake)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    await mod._run_reply_watch()
    assert {d for d, _ in captured["doms"]} == {"synigoros.gr", "dypa.gov.gr", "hli.gov.gr"}


# ── celery wrappers ──────────────────────────────────────────────────────────


def test_wrappers_delegate(mod, monkeypatch):
    async def _fu():
        return {"followups": 1}

    async def _rw():
        return {"watch": 1}

    monkeypatch.setattr(mod, "_run_followups", _fu)
    monkeypatch.setattr(mod, "_run_reply_watch", _rw)

    def _fake_run_async(coro, **k):
        name = getattr(coro, "cr_code", None) and coro.cr_code.co_name
        coro.close()  # consume without awaiting — avoids RuntimeWarning
        return {"wrapped": name}

    monkeypatch.setattr(mod, "run_async", _fake_run_async)
    assert mod.run_synigoros_followups() == {"wrapped": "_fu"}
    assert mod.watch_synigoros_replies() == {"wrapped": "_rw"}


# ── imap lane ────────────────────────────────────────────────────────────────


class _FakeIMAP:
    def __init__(self, uids=(b"1",), fetch_parts=None):
        self._uids = uids
        self._fetch = fetch_parts
        self.logged_out = False

    def login(self, u, p):
        pass

    def select(self, box):
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(self._uids)])
        # FETCH
        return ("OK", self._fetch if self._fetch is not None else [
            (b"1 (UID 1 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {123}",
             b"From: dypa@dypa.gov.gr\r\nSubject: New decision\r\nDate: Thu, 13 Nov 2026\r\n\r\n"),
            b")",
        ])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b""])


def _imap_env(mod, monkeypatch, fake_mail):
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda h, p: fake_mail)
    monkeypatch.setattr(
        mod, "_cfg",
        lambda k, d="": {"IMAP_PASSWORD": "pw", "IMAP_HOST": "h", "IMAP_PORT": "993"}.get(k, d),
    )


def test_imap_hits_returns_interesting(mod, monkeypatch):
    mail = _FakeIMAP()
    _imap_env(mod, monkeypatch, mail)
    hits = mod._imap_hits("dypa.gov.gr", "2026-11-01", None)
    assert len(hits) == 1
    assert hits[0]["lane"] == "cloudless" and "New decision" in hits[0]["subject"]
    assert mail.logged_out


def test_imap_hits_require_filters(mod, monkeypatch):
    mail = _FakeIMAP()
    _imap_env(mod, monkeypatch, mail)
    hits = mod._imap_hits("dypa.gov.gr", "2026-11-01", mod.re.compile("απόρριψ", mod.re.I))
    assert hits == []  # subject lacks the require pattern


def test_imap_hits_empty_and_noise(mod, monkeypatch):
    mail = _FakeIMAP(uids=())
    _imap_env(mod, monkeypatch, mail)
    assert mod._imap_hits("x.gr", "2026-01-01", None) == []

    mail2 = _FakeIMAP(fetch_parts=[
        (b"1 (UID 1 HEADER {50}", b"From: noreply@x.gr\r\nSubject: sign-in alert\r\n\r\n"),
        b")",
    ])
    _imap_env(mod, monkeypatch, mail2)
    assert mod._imap_hits("x.gr", "2026-01-01", None) == []


# ── _scan_replies_sync ───────────────────────────────────────────────────────


def test_scan_replies_sync_both_lanes(mod, monkeypatch):
    mail = _FakeIMAP()
    _imap_env(mod, monkeypatch, mail)

    def fake_gmail(path):
        if "messages?q" in path:
            return {"messages": [{"id": "g1", "snippet": "snip", "threadId": "t1"}]}
        return {"payload": {"headers": [
            {"name": "From", "value": "sec@hli.gov.gr"},
            {"name": "Subject", "value": "Update on your request"},
            {"name": "Date", "value": "Fri, 14 Nov 2026"},
        ]}}

    monkeypatch.setattr(mod, "_gmail_get", fake_gmail)
    domains = [("dypa.gov.gr", None), ("hli.gov.gr", "request")]
    fresh = mod._scan_replies_sync(domains, "2026-11-01")
    lanes = {m["lane"] for m in fresh}
    assert "cloudless" in lanes and "gmail" in lanes
    gmail_hit = next(m for m in fresh if m["lane"] == "gmail")
    assert gmail_hit["key"] == "gmail:g1" and gmail_hit["link"].endswith("/t1")


def test_scan_replies_sync_gmail_require_skips(mod, monkeypatch):
    monkeypatch.setattr(mod, "_cfg", lambda k, d="": "")  # no IMAP pw -> IMAP lane skipped
    monkeypatch.setattr(
        mod, "_gmail_get",
        lambda path: {"messages": [{"id": "g2"}]} if "messages?q" in path else {
            "payload": {"headers": [
                {"name": "From", "value": "a@dypa.gov.gr"},
                {"name": "Subject", "value": "Unrelated newsletter"},
            ]}
        },
    )
    fresh = mod._scan_replies_sync([("dypa.gov.gr", "απόρριψ")], "2026-11-01")
    assert fresh == []


def test_scan_replies_sync_lane_errors_isolated(mod, monkeypatch):
    def _boom(*a):
        raise RuntimeError("imap dead")

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", _boom)
    monkeypatch.setattr(mod, "_cfg", lambda k, d="": {"IMAP_PASSWORD": "pw"}.get(k, d))
    monkeypatch.setattr(mod, "_gmail_get", lambda path: {"messages": []})
    # IMAP failure is caught; gmail lane still returns
    assert mod._scan_replies_sync([("x.gr", None)], "2026-01-01") == []


# ── followups reply gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followups_reply_gate_skips(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "rg", "sendAt": now.date().isoformat(), "file": "x", "to": ["a"],
          "skipIfRepliedFrom": "dypa.gov.gr"}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)

    def _replied(*a):
        return True, {"subject": "Re: case"}, None

    monkeypatch.setattr(mod, "_replied", _replied)
    alerts = []

    async def _alert(*a):
        alerts.append(a)
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "skipped-reply"


@pytest.mark.asyncio
async def test_followups_reply_gate_error_sends_anyway(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "rge", "sendAt": now.date().isoformat(), "file": "x", "to": ["a"],
          "skipIfRepliedFrom": "dypa.gov.gr"}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)

    def _replied(*a):
        return False, None, "imap down"

    monkeypatch.setattr(mod, "_replied", _replied)
    monkeypatch.setattr(mod, "_read_body", lambda p: ("s", "b"))
    sent = []
    monkeypatch.setattr(mod, "_smtp_send", lambda *a: sent.append(a))

    async def _alert(*a):
        return True

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "sent" and sent


@pytest.mark.asyncio
async def test_followups_tg_failed_branch(mod, monkeypatch):
    now = mod.datetime.now(mod.ATHENS)
    ev = {"id": "tgf", "sendAt": now.date().isoformat(), "tgOnly": True}
    monkeypatch.setattr(mod, "_load_json", lambda *a, **k: {"events": [ev]})
    fake = _FakeRedis()
    _patch_async_redis(mod, monkeypatch, fake)

    async def _alert(*a):
        return False

    monkeypatch.setattr(mod, "send_telegram_alert", _alert)
    monkeypatch.setattr(mod, "_record", AsyncMock())
    out = await mod._run_followups()
    assert out["events"][0]["result"] == "tg-failed"
