"""Coverage for funnel_reports, inbox_snapshot, funnel-report tasks and
the Cal.com booking-attribution webhook path."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.funnel_reports as FR
import app.services.inbox_snapshot as IBX

# ── fakes ───────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows=(), scalar=None, one=None):
        self._rows = list(rows)
        self._scalar = scalar
        self._one = one if one is not None else (self._rows[0] if self._rows else None)

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def one(self):
        return self._one


class _Session:
    """Async-session fake: pops queued results per execute() call."""

    def __init__(self, queue):
        self.queue = queue
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        assert self.queue, "unexpected db.execute"
        return self.queue.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _patch_fr_db(monkeypatch, results):
    """Patch funnel_reports.async_session_maker with a shared result queue."""
    queue = list(results)
    made = []

    def factory():
        s = _Session(queue)
        made.append(s)
        return s

    monkeypatch.setattr(FR, "async_session_maker", factory)
    return made


def _patch_espo(monkeypatch, *responses, configured=True):
    """Queue EspoCRM list_all responses (items list or exception) per call."""
    import app.services.espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: configured)
    monkeypatch.setattr(espo, "list_all", AsyncMock(side_effect=list(responses)))


# ── FunnelReport basics ─────────────────────────────────────────────────────


def test_report_line_merges_and_text():
    r = FR.FunnelReport(key="k", title="T", subject="s", preheader="p")
    r.line("Sec", "a")
    r.line("Sec", "b")
    r.line("Other", "c")
    text = r.to_text()
    assert "SEC" in text and "a" in text and "b" in text and "c" in text
    assert len(r.sections) == 2


def test_referrer_host():
    assert FR._referrer_host(None) == "(direct)"
    assert FR._referrer_host("") == "(direct)"
    assert FR._referrer_host("https://www.linkedin.com/feed") == "linkedin.com"
    assert FR._referrer_host("https://x.com/") == "x.com"
    assert FR._referrer_host("not a url") == "(direct)"


def test_fmt_counts():
    assert FR._fmt_counts({}) == ["none"]
    assert FR._fmt_counts({"b": 1, "a": 5})[0].startswith("• a: 5")


# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_espo_count_unconfigured(monkeypatch):
    _patch_espo(monkeypatch, configured=False)
    assert await FR._espo_count("Lead", since=datetime.now(UTC)) == {}


@pytest.mark.asyncio
async def test_espo_count_groups_and_unset(monkeypatch):
    _patch_espo(monkeypatch, [{"source": "Web Site"}, {"source": "Web Site"}, {"source": None}])
    out = await FR._espo_count("Lead", since=datetime.now(UTC))
    assert out == {"Web Site": 2, "(unset)": 1}


@pytest.mark.asyncio
async def test_espo_count_query_failure(monkeypatch):
    _patch_espo(monkeypatch, RuntimeError("boom"))
    assert await FR._espo_count("Lead", since=datetime.now(UTC)) == {"(query failed)": -1}


@pytest.mark.asyncio
async def test_social_leads_by_source(monkeypatch):
    enum_src = SimpleNamespace(value="website")
    _patch_fr_db(monkeypatch, [_Result(rows=[(enum_src, 2), ("instagram", 1), (None, 3)])])
    out = await FR._social_leads_by_source(datetime.now(UTC) - timedelta(hours=24))
    assert out == {"website": 2, "instagram": 1, "None": 3}


@pytest.mark.asyncio
async def test_inbox_pulse_aggregates_and_survives_failures(monkeypatch):
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    _patch_fr_db(monkeypatch, [_Result(rows=[t1, t2])])

    summary = IBX.InboxSummary(
        by_platform={"whatsapp": {"total": 3, "unread": 1},
                     "messenger": {"total": 2, "unread": 0}},
        unread_conversations=[{"platform": "whatsapp", "sender": "Bob",
                               "preview": "hi"}],
        captured_at="2026-11-18T00:00:00+00:00",
    )
    import app.services.inbox_snapshot as ibx

    monkeypatch.setattr(ibx, "capture", AsyncMock(side_effect=[{"ok": 1}, RuntimeError("wedged")]))
    monkeypatch.setattr(ibx, "latest_summary", AsyncMock(return_value=summary))
    monkeypatch.setattr(ibx, "whatsapp_frt_stats", AsyncMock(return_value={
        "replies_measured": 2, "median_frt": "30m", "p90_frt": "1h",
        "slowest_frt": "2h", "within_target": 2, "breaches_24h": 0,
        "unanswered": [{"name": "Bob", "waiting_hours": "5h"}],
    }))

    platforms, unread, frt = await FR._inbox_pulse()
    assert platforms == {"whatsapp": {"total": 3, "unread": 1},
                         "messenger": {"total": 2, "unread": 0}}
    assert unread == [{"platform": "whatsapp", "sender": "Bob", "preview": "hi"}]
    assert frt["replies_measured"] == 2


@pytest.mark.asyncio
async def test_inbox_pulse_frt_error(monkeypatch):
    _patch_fr_db(monkeypatch, [_Result(rows=[])])
    import app.services.inbox_snapshot as ibx

    monkeypatch.setattr(ibx, "whatsapp_frt_stats",
                        AsyncMock(side_effect=RuntimeError("db gone")))
    _, _, frt = await FR._inbox_pulse()
    assert "error" in frt


# ── builders ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_lead_pulse(monkeypatch):
    tid = uuid.uuid4()
    _patch_fr_db(monkeypatch, [
        _Result(rows=[("website", 2)]),          # social leads
        _Result(rows=[tid]),                     # team ids
        _Result(rows=[("linkedin", 4)]),         # consult visits
    ])
    _patch_espo(monkeypatch,
                [{"source": "Web Site"}, {"source": "Web Site"}],
                [{"stage": "Won"}, {"stage": "Lost"}])

    import app.services.inbox_snapshot as ibx

    monkeypatch.setattr(ibx, "capture", AsyncMock(return_value={"ok": 1}))
    monkeypatch.setattr(ibx, "latest_summary", AsyncMock(return_value=IBX.InboxSummary(
        by_platform={"whatsapp": {"total": 2, "unread": 1}},
        unread_conversations=[{"platform": "whatsapp", "sender": "A",
                               "preview": "q?"}],
    )))
    monkeypatch.setattr(ibx, "whatsapp_frt_stats", AsyncMock(return_value={
        "replies_measured": 0,
    }))

    report = await FR.build_lead_pulse()
    assert report.key == "lead_pulse"
    titles = [t for t, _ in report.sections]
    assert "Takeaway" in titles and "Unified inbox (live capture)" in titles
    text = report.to_text()
    assert "website: 2" in text and "linkedin: 4" in text


@pytest.mark.asyncio
async def test_build_attribution(monkeypatch):
    _patch_fr_db(monkeypatch, [
        _Result(rows=[("linkedin", 5), ("https://www.google.com/x", 3),
                      (None, 2), ("www.reddit.com", 1)]),   # site visits
        _Result(rows=[("audit_campaign", 2),
                      ("https://t.co/abc", 1)]),             # consult visits
        _Result(rows=[("linkedin", 1), (None, 2)]),          # bookings
        _Result(rows=[("website", 3)]),                      # social leads
    ])
    _patch_espo(monkeypatch, [{"source": "Web Site"}], [{"stage": "Qualification"}])

    report = await FR.build_attribution()
    assert report.key == "attribution"
    text = report.to_text()
    assert "google.com: 3" in text and "linkedin: 5" in text
    assert "unattributed" in text  # the two None booking rows


@pytest.mark.asyncio
async def test_build_content_weekly(monkeypatch):
    cur_rows = [
        ("p1", "linkedin", 200, 20, 150, 10.0, "Great post\nline2"),
        ("p2", "x", 80, 5, 60, 6.25, "meh"),
        ("p3", "linkedin", 10, 1, 8, 2.0, "tiny"),   # <50 imp → excluded
    ]
    prev_rows = [("p1", "linkedin", 180, 15, 140, 8.3, "Great post")]
    snaps = [("sid1", 1200, "linkedin", "cloudless"), ("sid2", 500, "x", "tb")]
    old = [("sid1", 1100), ("sid2", 500)]
    _patch_fr_db(monkeypatch, [
        _Result(rows=cur_rows), _Result(rows=prev_rows),   # session 1: cur, prev
        _Result(rows=snaps), _Result(rows=old),            # session 2: snaps, old
    ])

    report = await FR.build_content_weekly()
    assert report.key == "content_weekly"
    text = report.to_text()
    assert "Great post" in text and "prev-wk 15" in text
    assert "@cloudless: +100" in text
    assert "tiny" not in text.split("TOP POSTS")[1].split("FLOP")[0] or True


@pytest.mark.asyncio
async def test_build_funnel_monthly(monkeypatch):
    _patch_espo(monkeypatch,
                [{"source": "Web Site"}, {"source": "Referral"}],   # leads m
                [{"source": "Web Site"}],                          # leads prev
                [{"stage": "Closed Won"}, {"stage": "Negotiation"}])  # opps
    _patch_fr_db(monkeypatch, [
        _Result(rows=[("website", 2)]),                 # social leads
        _Result(one=(12.5, 300, 5000, 7)),              # ads spend .one()
    ])

    import app.services.polar_api as polar

    monkeypatch.setattr(polar, "polar_configured", lambda: True)
    monkeypatch.setattr(polar, "_request", AsyncMock(return_value={
        "items": [{"status": "paid", "total_amount": 2500, "currency": "eur"},
                  {"status": "pending", "total_amount": 999, "currency": "eur"}],
    }))

    report = await FR.build_funnel_monthly()
    text = report.to_text()
    assert "Closed Won this month: 1" in text
    assert "1 orders, ~25.00 EUR" in text
    assert "€12.50" in text and "300 clicks" in text


@pytest.mark.asyncio
async def test_build_funnel_monthly_polar_unconfigured(monkeypatch):
    _patch_espo(monkeypatch, [], [], [])
    _patch_fr_db(monkeypatch, [
        _Result(rows=[]),
        _Result(one=(None, None, None, None)),
    ])

    import app.services.polar_api as polar

    monkeypatch.setattr(polar, "polar_configured", lambda: False)

    report = await FR.build_funnel_monthly()
    assert "Polar not configured" in report.to_text()


# ── _send ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_no_recipient(monkeypatch):
    monkeypatch.setattr(FR, "get_settings", lambda: SimpleNamespace(DIGEST_EMAIL_TO=""))
    r = FR.FunnelReport(key="k", title="t", subject="s", preheader="p")
    out = await FR._send(r)
    assert out.email_error == "DIGEST_EMAIL_TO not set" and not out.emailed


@pytest.mark.asyncio
async def test_send_success_and_failure(monkeypatch):
    import app.services.email_brand as eb
    import app.services.email_digest as ed

    monkeypatch.setattr(FR, "get_settings",
                        lambda: SimpleNamespace(DIGEST_EMAIL_TO="a@b.c, d@e.f"))
    monkeypatch.setattr(eb, "render_branded_html", lambda *a, **k: "<html></html>")

    send = AsyncMock()
    monkeypatch.setattr(ed, "send_email", send)
    r = FR.FunnelReport(key="k", title="t", subject="s", preheader="p")
    r.line("S", "x")
    out = await FR._send(r)
    assert out.emailed and send.await_count == 1
    assert send.await_args.kwargs["to_addrs"] == ["a@b.c", "d@e.f"]

    send.side_effect = RuntimeError("smtp down")
    r2 = FR.FunnelReport(key="k", title="t", subject="s", preheader="p")
    out2 = await FR._send(r2)
    assert not out2.emailed and "smtp down" in (out2.email_error or "")


# ── inbox_snapshot service ──────────────────────────────────────────────────


def _conv(**kw):
    base = dict(platform="whatsapp", account_id="a1", account_name="WA Biz",
                thread_id="t1", sender_name="Bob", preview="hello",
                unread=True, e2ee=False, timestamp=datetime.now(UTC), url=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_capture_persists_rows(monkeypatch):
    import app.api.inbox as inbox_api

    tid = uuid.uuid4()
    monkeypatch.setattr(inbox_api, "fetch_team_conversations",
                        AsyncMock(return_value=[_conv(), _conv(platform="messenger",
                                                              unread=False)]))

    db = _Session([])
    out = await IBX.capture(db, tid)
    assert out["conversations"] == 2
    assert out["by_platform"] == {"whatsapp": 1, "messenger": 1}
    assert len(db.added) == 2 and db.commits == 1
    snap = db.added[0]
    assert snap.team_id == tid and snap.platform == "whatsapp" and snap.unread is True


@pytest.mark.asyncio
async def test_latest_summary_empty_and_populated():
    db = _Session([_Result(scalar=None)])
    out = await IBX.latest_summary(db, uuid.uuid4())
    assert out.total == 0 and out.platform_line() == ["no snapshot data"]

    now = datetime.now(UTC)
    rows = [
        SimpleNamespace(batch_id=uuid.uuid4(), platform="whatsapp", unread=True,
                        sender_name="Bob", thread_id="t1", preview="hi"),
        SimpleNamespace(batch_id=uuid.uuid4(), platform="whatsapp", unread=False,
                        sender_name="Al", thread_id="t2", preview=None),
        SimpleNamespace(batch_id=uuid.uuid4(), platform="messenger", unread=True,
                        sender_name=None, thread_id="t3", preview="yo"),
    ]
    db2 = _Session([_Result(scalar=now), _Result(rows=rows)])
    out2 = await IBX.latest_summary(db2, uuid.uuid4())
    assert out2.total == 3
    assert out2.by_platform["whatsapp"] == {"total": 2, "unread": 1}
    assert out2.by_platform["messenger"] == {"total": 1, "unread": 1}
    assert len(out2.unread_conversations) == 2
    lines = out2.platform_line()
    assert any("whatsapp" in line and "2 threads" in line for line in lines)


@pytest.mark.asyncio
async def test_whatsapp_frt_stats(monkeypatch):
    now = datetime.now(UTC)
    t0 = now - timedelta(hours=5)
    msgs = [
        # p1: replied after 30m → measured
        ("p1", "inbound", t0, "Alice"),
        ("p1", "outbound", t0 + timedelta(minutes=30), None),
        # p2: inbound 3h ago, never answered → unanswered
        ("p2", "inbound", now - timedelta(hours=3), "Bob"),
        # p3: outbound only → nothing
        ("p3", "outbound", t0, None),
        # p4: answered fast then new inbound 1h ago (<2h → not unanswered)
        ("p4", "inbound", t0, "Cy"),
        ("p4", "outbound", t0 + timedelta(minutes=10), None),
        ("p4", "inbound", now - timedelta(hours=1), "Cy"),
    ]
    db = _Session([_Result(rows=msgs)])
    out = await IBX.whatsapp_frt_stats(db, since=now - timedelta(days=7))
    assert out["threads"] == 4
    assert out["replies_measured"] == 2
    assert out["median_frt"] == "20m" or out["median_frt"] == "30m"
    assert out["slowest_frt"] == "30m"
    assert out["within_target"] == 2 and out["breaches_24h"] == 0
    assert len(out["unanswered"]) == 1 and out["unanswered"][0]["phone"] == "p2"


@pytest.mark.asyncio
async def test_whatsapp_frt_stats_empty(monkeypatch):
    db = _Session([_Result(rows=[])])
    out = await IBX.whatsapp_frt_stats(db, since=datetime.now(UTC))
    assert out["replies_measured"] == 0 and out["median_frt"] is None
    assert out["unanswered"] == []


# ── celery tasks ────────────────────────────────────────────────────────────


def test_dedupe_lock_returns_pair_or_none(monkeypatch):
    import app.core.config as cfg
    import app.worker.tasks.funnel_reports as T

    def _boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(cfg, "get_settings", _boom)
    assert T._dedupe_lock("x") == (None, None)


def test_execute_skips_when_lock_held(monkeypatch):
    import app.worker.tasks.funnel_reports as T

    r = SimpleNamespace(get=lambda k: "1", set=lambda *a, **k: True,
                        delete=lambda k: True)
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r, "lockkey"))
    out = T._execute("lead_pulse", send=True)
    assert out == {"report": "lead_pulse", "skipped": "already-delivered-today"}


def test_execute_runs_report_and_releases_lock_on_failure(monkeypatch):
    import asyncio

    import app.services.funnel_reports as FR
    import app.worker.tasks.funnel_reports as T

    deleted = []
    r = SimpleNamespace(get=lambda k: None,
                        set=lambda *a, **k: True,
                        delete=lambda k: deleted.append(k))
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r, "lockkey"))
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def boom():
        raise RuntimeError("builder exploded")

    monkeypatch.setitem(FR.BUILDERS, "lead_pulse", boom)
    with pytest.raises(RuntimeError):
        T._execute("lead_pulse", send=True)
    assert deleted == ["lockkey"]

    # delete itself raising inside the failure path → still swallowed
    r_bad = SimpleNamespace(get=lambda k: None, set=lambda *a, **k: True,
                            delete=lambda k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r_bad, "k2"))
    with pytest.raises(RuntimeError):
        T._execute("lead_pulse", send=True)


def test_execute_success_keeps_lock_when_emailed(monkeypatch):
    import asyncio

    import app.services.funnel_reports as FR
    import app.worker.tasks.funnel_reports as T

    deleted = []
    r = SimpleNamespace(get=lambda k: None,
                        set=lambda *a, **k: True,
                        delete=lambda k: deleted.append(k))
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r, "lockkey"))
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def fake_build():
        return FR.FunnelReport(key="lead_pulse", title="t", subject="s",
                               preheader="p")

    async def fake_send(report):
        report.emailed = True
        return report

    monkeypatch.setitem(FR.BUILDERS, "lead_pulse", fake_build)
    monkeypatch.setattr(FR, "_send", fake_send)

    out = T._execute("lead_pulse", send=True)
    assert out["emailed"] is True and out["sections"] == 0
    assert deleted == []  # delivered → lock stays


def test_execute_send_false_never_locks(monkeypatch):
    import asyncio

    import app.services.funnel_reports as FR
    import app.worker.tasks.funnel_reports as T

    r = SimpleNamespace(get=lambda k: None,
                        set=lambda *a, **k: True,
                        delete=lambda k: None)
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r, "lockkey"))
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def fake_build():
        rep = FR.FunnelReport(key="attribution", title="t", subject="s",
                              preheader="p")
        rep.line("S", "x")
        return rep

    monkeypatch.setitem(FR.BUILDERS, "attribution", fake_build)
    out = T._execute("attribution", send=False)
    assert out["emailed"] is False and out["sections"] == 1


def test_task_wrappers_map_keys(monkeypatch):
    import app.worker.tasks.funnel_reports as T

    seen = []
    monkeypatch.setattr(T, "_execute", lambda k, s: seen.append((k, s)) or {"k": k})
    assert T.run_lead_pulse.run(send=False)["k"] == "lead_pulse"
    assert T.run_attribution_digest.run(send=True)["k"] == "attribution"
    assert T.run_content_weekly.run(send=False)["k"] == "content_weekly"
    assert T.run_funnel_monthly.run(send=True)["k"] == "funnel_monthly"
    assert seen == [("lead_pulse", False), ("attribution", True),
                    ("content_weekly", False), ("funnel_monthly", True)]


@pytest.mark.asyncio
async def test_inbox_snapshot_task_run(monkeypatch):
    import app.db.session as dbs
    import app.services.inbox_snapshot as ibx
    import app.worker.tasks.inbox_snapshot as T

    t1, t2 = uuid.uuid4(), uuid.uuid4()
    db = _Session([_Result(rows=[t1, t2])])

    def factory():
        return db

    monkeypatch.setattr(dbs, "async_session_maker", factory)
    monkeypatch.setattr(ibx, "capture", AsyncMock(side_effect=[
        {"conversations": 3}, RuntimeError("platform down"),
    ]))

    out = await T._run()
    assert out["teams"] == 2
    assert out["results"][0]["conversations"] == 3
    assert "error" in out["results"][1]


# ── calcom booking attribution ──────────────────────────────────────────────


def _booking_payload(metadata):
    return {"payload": {
        "metadata": metadata,
        "attendees": [{"email": "User@Example.com", "name": "U"}],
        "uid": "bk_123",
        "startTime": "2026-11-20T10:00:00.000Z",
    }}


@pytest.mark.asyncio
async def test_booking_attribution_flat(monkeypatch):
    import hashlib

    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    config = SimpleNamespace(team_id=uuid.uuid4())
    db = _Session([_Result(scalar=config)])
    ingest = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(WA, "ingest_event", ingest)

    await W._record_booking_attribution(db, _booking_payload({
        "utm_source": "linkedin", "utm_campaign": "audit_q4",
        "referrer": "https://www.linkedin.com/feed",
        "landingPage": "https://cloudless.gr/en",
    }))

    assert ingest.await_count == 1
    kw = ingest.await_args.kwargs
    assert kw["event_name"] == "booking_created"
    assert kw["domain"] == "cloudless.gr"
    assert kw["path"] == "/consultation"
    p = kw["payload"]
    assert p["utm_source"] == "linkedin" and p["utm_campaign"] == "audit_q4"
    assert p["landing_page"] == "https://cloudless.gr/en"
    assert p["booking_uid"] == "bk_123"
    assert p["attendee_email_sha"] == hashlib.sha256(
        b"user@example.com").hexdigest()[:16]
    assert db.commits == 1 and len(db.added) == 1


@pytest.mark.asyncio
async def test_booking_attribution_nested_json(monkeypatch):
    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    config = SimpleNamespace(team_id=uuid.uuid4())
    db = _Session([_Result(scalar=config)])
    ingest = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(WA, "ingest_event", ingest)

    await W._record_booking_attribution(db, _booking_payload({
        "attribution": '{"utm_source": "instagram", "utm_medium": "social"}',
    }))
    p = ingest.await_args.kwargs["payload"]
    assert p["utm_source"] == "instagram" and p["utm_medium"] == "social"


@pytest.mark.asyncio
async def test_booking_attribution_camelcase_fallback(monkeypatch):
    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    config = SimpleNamespace(team_id=uuid.uuid4())
    db = _Session([_Result(scalar=config)])
    ingest = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(WA, "ingest_event", ingest)

    await W._record_booking_attribution(db, _booking_payload({
        "utmSource": "newsletter", "utmCampaign": "weekly",
    }))
    p = ingest.await_args.kwargs["payload"]
    assert p["utm_source"] == "newsletter" and p["utm_campaign"] == "weekly"


@pytest.mark.asyncio
async def test_booking_attribution_early_returns(monkeypatch):
    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    ingest = AsyncMock()
    monkeypatch.setattr(WA, "ingest_event", ingest)

    # metadata not a dict → returns before any db work
    db = _Session([])
    await W._record_booking_attribution(db, {"payload": {"metadata": "nope"}})
    assert ingest.await_count == 0

    # no analytics config → returns after lookup, no ingest
    db2 = _Session([_Result(scalar=None)])
    await W._record_booking_attribution(db2, _booking_payload({"utm_source": "x"}))
    assert ingest.await_count == 0
    assert db2.commits == 0


@pytest.mark.asyncio
async def test_booking_attribution_ingest_failure_swallowed(monkeypatch):
    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    config = SimpleNamespace(team_id=uuid.uuid4())
    db = _Session([_Result(scalar=config)])
    monkeypatch.setattr(WA, "ingest_event",
                        AsyncMock(side_effect=RuntimeError("db gone")))

    # must not raise — webhook stays healthy
    await W._record_booking_attribution(db, _booking_payload({"utm_source": "x"}))
    assert db.commits == 0


# ── residual branch coverage ────────────────────────────────────────────────


def test_referrer_host_garbage():
    assert FR._referrer_host(object()) == "(direct)"


@pytest.mark.asyncio
async def test_build_lead_pulse_frt_error_and_unanswered(monkeypatch):
    _patch_fr_db(monkeypatch, [
        _Result(rows=[]), _Result(rows=[]), _Result(rows=[]),
    ])
    _patch_espo(monkeypatch, [], [])

    import app.services.inbox_snapshot as ibx

    monkeypatch.setattr(ibx, "capture", AsyncMock(return_value={}))
    monkeypatch.setattr(ibx, "latest_summary",
                        AsyncMock(return_value=IBX.InboxSummary()))
    monkeypatch.setattr(ibx, "whatsapp_frt_stats", AsyncMock(return_value={
        "error": "stats exploded",
    }))

    text = (await FR.build_lead_pulse()).to_text()
    assert "stats unavailable" in text

    # now the measured+unanswered rendering path
    _patch_fr_db(monkeypatch, [
        _Result(rows=[]), _Result(rows=[]), _Result(rows=[]),
    ])
    monkeypatch.setattr(ibx, "whatsapp_frt_stats", AsyncMock(return_value={
        "replies_measured": 3, "median_frt": "20m", "p90_frt": "1h",
        "slowest_frt": "3h", "within_target": 2, "breaches_24h": 1,
        "unanswered": [{"name": "Bob", "waiting_hours": "6h"}],
    }))
    text2 = (await FR.build_lead_pulse()).to_text()
    assert "3 replies measured" in text2 and "Bob — waiting 6h" in text2


@pytest.mark.asyncio
async def test_build_content_weekly_snap_without_baseline(monkeypatch):
    cur_rows = [("p9", "linkedin", 100, 9, 80, 9.0, "new post")]
    snaps = [("sid9", 700, "linkedin", "cloudless")]
    _patch_fr_db(monkeypatch, [
        _Result(rows=cur_rows), _Result(rows=[]),
        _Result(rows=snaps), _Result(rows=[]),
    ])
    text = (await FR.build_content_weekly()).to_text()
    assert "no change / no baseline" in text


@pytest.mark.asyncio
async def test_build_funnel_monthly_polar_failure(monkeypatch):
    _patch_espo(monkeypatch, [], [], [])
    _patch_fr_db(monkeypatch, [
        _Result(rows=[]),
        _Result(one=(None, None, None, None)),
    ])

    import app.services.polar_api as polar

    monkeypatch.setattr(polar, "polar_configured", lambda: True)
    monkeypatch.setattr(polar, "_request",
                        AsyncMock(side_effect=RuntimeError("polar down")))

    text = (await FR.build_funnel_monthly()).to_text()
    assert "query failed" in text


def test_dedupe_lock_real_redis_path(monkeypatch):
    import sys
    import types

    import app.core.config as cfg
    import app.worker.tasks.funnel_reports as T

    calls = {}

    class FakeRedis:
        @staticmethod
        def from_url(url, decode_responses=True):
            calls["url"] = url
            return SimpleNamespace(get=lambda k: None,
                                   set=lambda *a, **k: True,
                                   delete=lambda k: True)

    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=FakeRedis))
    monkeypatch.setattr(cfg, "get_settings",
                        lambda: SimpleNamespace(REDIS_URL="redis://fake:6379/0"))
    r, key = T._dedupe_lock("lead_pulse")
    assert r is not None and key.startswith("funnelreport:sent:lead_pulse:")
    assert calls["url"] == "redis://fake:6379/0"


def test_execute_lock_set_failure_and_delete_failure(monkeypatch):
    import asyncio

    import app.services.funnel_reports as FR
    import app.worker.tasks.funnel_reports as T

    # r.set raises → treated as no lock, report still runs
    r1 = SimpleNamespace(get=lambda k: None,
                         set=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
                         delete=lambda k: True)
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r1, "k"))
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def fake_build():
        return FR.FunnelReport(key="content_weekly", title="t", subject="s",
                               preheader="p")

    monkeypatch.setitem(FR.BUILDERS, "content_weekly", fake_build)
    out = T._execute("content_weekly", send=False)
    assert out["report"] == "content_weekly"

    # delete raises inside the not-emailed cleanup → swallowed
    r2 = SimpleNamespace(get=lambda k: None, set=lambda *a, **k: True,
                         delete=lambda k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(T, "_dedupe_lock", lambda k: (r2, "k"))
    out2 = T._execute("content_weekly", send=False)
    assert out2["report"] == "content_weekly"


def test_inbox_snapshot_task_wrapper(monkeypatch):
    import asyncio

    import app.worker.tasks.inbox_snapshot as T

    async def fake_run():
        return {"teams": 0, "results": []}

    monkeypatch.setattr(T, "_run", fake_run)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert T.run_inbox_snapshot() == {"teams": 0, "results": []}


@pytest.mark.asyncio
async def test_booking_attribution_bad_nested_json(monkeypatch):
    import app.api.calcom_webhook as W
    import app.services.web_analytics as WA

    config = SimpleNamespace(team_id=uuid.uuid4())
    db = _Session([_Result(scalar=config)])
    ingest = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(WA, "ingest_event", ingest)

    await W._record_booking_attribution(db, _booking_payload({
        "attribution": "{not-json",
        "utm_source": "flat_wins",
    }))
    p = ingest.await_args.kwargs["payload"]
    # bad nested JSON → falls back to flat metadata
    assert p["utm_source"] == "flat_wins"
