"""Coverage for the funnel-report suite added 2026-11-18.

Targets the zero-coverage modules that dropped the line-coverage gate:
app.services.funnel_reports helpers, app.services.inbox_snapshot (capture /
latest_summary / whatsapp_frt_stats), and the worker task wrappers
(dedupe lock + run paths). DB and provider boundaries are faked; the
report/statistics logic runs for real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import funnel_reports as FR
from app.services import inbox_snapshot as IS
from app.worker.tasks import funnel_reports as FT
from app.worker.tasks import inbox_snapshot as IT

# ---------------------------------------------------------------- FunnelReport


def test_funnel_report_line_and_text():
    r = FR.FunnelReport(key="k", title="My Title", subject="s", preheader="p")
    r.line("Sec A", "one", "two")
    r.line("Sec A", "three")
    r.line("Sec B")
    text = r.to_text()
    assert "My Title" in text
    assert "SEC A" in text
    assert "one" in text and "three" in text
    assert "(none)" in text  # Sec B had no lines
    # second line() call to an existing section appends, not duplicates
    assert text.count("SEC A") == 1


def test_funnel_report_to_html():
    r = FR.FunnelReport(key="k", title="T", subject="subj", preheader="pre")
    r.line("S", "body line")
    html = r.to_html()
    assert "body line" in html
    assert "subj" in html


def test_since_hours_and_referrer_host():
    now = datetime.now(UTC)
    assert FR._since_hours(24) <= now
    assert FR._since_hours(1) > FR._since_hours(24)
    assert FR._referrer_host("https://www.example.com/path?q=1") == "example.com"
    assert FR._referrer_host("not a url") == "(direct)"
    assert FR._referrer_host(None) == "(direct)"


def test_fmt_counts():
    assert FR._fmt_counts({}) == ["none"]
    assert FR._fmt_counts({"a": 2, "b": 1}, empty="-") != ["none"]
    out = FR._fmt_counts({"fb": 3, "ig": 1})
    assert out[0].startswith("• fb: 3")  # sorted by count desc


# ------------------------------------------------------------- inbox_snapshot


def test_inbox_summary_platform_line():
    s = IS.InboxSummary()
    assert s.platform_line() == ["no snapshot data"]
    s.by_platform = {"messenger": {"total": 4, "unread": 2}, "instagram": {"total": 1, "unread": 0}}
    lines = s.platform_line()
    assert len(lines) == 2
    assert any("messenger" in ln and "4 threads" in ln for ln in lines)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _ScaleResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _AllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """db.execute returns queued results in call order; records add/commit."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_latest_summary_empty_and_populated():
    team = uuid.uuid4()
    # no snapshot yet
    db = _FakeDB([_ScalarResult(None)])
    s = await IS.latest_summary(db, team)
    assert s.total == 0 and s.batch_id is None

    # populated batch
    newest = datetime.now(UTC)
    batch = uuid.uuid4()
    rows = [
        SimpleNamespace(batch_id=batch, platform="messenger", unread=True,
                        sender_name="Alice", thread_id="t1", preview="hi"),
        SimpleNamespace(batch_id=batch, platform="messenger", unread=False,
                        sender_name="Bob", thread_id="t2", preview=None),
        SimpleNamespace(batch_id=batch, platform="instagram", unread=True,
                        sender_name=None, thread_id="ig9", preview="yo"),
    ]
    db = _FakeDB([_ScalarResult(newest), _ScaleResult(rows)])
    s = await IS.latest_summary(db, team)
    assert s.total == 3
    assert s.by_platform["messenger"] == {"total": 2, "unread": 1}
    assert s.by_platform["instagram"]["unread"] == 1
    assert len(s.unread_conversations) == 2
    assert s.unread_conversations[0]["sender"] == "Alice"
    # instagram row has no sender_name -> falls back to thread_id
    assert any(u["sender"] == "ig9" for u in s.unread_conversations)


@pytest.mark.asyncio
async def test_capture_persists_batch(monkeypatch):
    team = uuid.uuid4()
    convs = [
        SimpleNamespace(platform="messenger", account_id="a1", account_name="Page",
                        thread_id="t1", sender_name="A", preview="x" * 600,
                        unread=True, e2ee=False, timestamp=datetime.now(UTC), url="http://u"),
        SimpleNamespace(platform="whatsapp", account_id="a2", account_name=None,
                        thread_id="t2", sender_name=None, preview=None,
                        unread=False, e2ee=True, timestamp=None, url=None),
    ]
    import app.api.inbox as inbox_api

    monkeypatch.setattr(inbox_api, "fetch_team_conversations", AsyncMock(return_value=convs))
    db = _FakeDB([])
    out = await IS.capture(db, team)
    assert out["conversations"] == 2
    assert out["by_platform"] == {"messenger": 1, "whatsapp": 1}
    assert len(db.added) == 2 and db.commits == 1
    snap = db.added[0]
    assert snap.team_id == team and snap.platform == "messenger"
    assert snap.preview is not None and len(snap.preview) == 500  # truncated
    assert db.added[1].meta == {} and db.added[0].meta == {"url": "http://u"}


@pytest.mark.asyncio
async def test_whatsapp_frt_stats():
    now = datetime.now(UTC)
    rows = [
        # phone1: inbound -> outbound after 30m (within target)
        ("p1", "inbound", now - timedelta(hours=3), "Sara"),
        ("p1", "outbound", now - timedelta(hours=2, minutes=30), None),
        # phone2: inbound -> outbound after 30h (24h breach)
        ("p2", "inbound", now - timedelta(hours=40), "Yannis"),
        ("p2", "outbound", now - timedelta(hours=10), None),
        # phone3: inbound 5h ago, never answered (>2h -> unanswered)
        ("p3", "inbound", now - timedelta(hours=5), "Nikos"),
        # phone4: outbound only — no pending inbound
        ("p4", "outbound", now - timedelta(hours=1), None),
        # phone5: inbound 30min ago — pending but under 2h -> not unanswered
        ("p5", "inbound", now - timedelta(minutes=30), None),
    ]
    db = _FakeDB([_AllResult(rows)])
    stats = await IS.whatsapp_frt_stats(db, since=now - timedelta(days=7))
    assert stats["threads"] == 5
    assert stats["replies_measured"] == 2
    assert stats["within_target"] == 1
    assert stats["breaches_24h"] == 1
    assert stats["median_frt"] is not None
    assert stats["unanswered"] and stats["unanswered"][0]["phone"] == "p3"
    assert stats["unanswered"][0]["name"] == "Nikos"

    # empty window
    db = _FakeDB([_AllResult([])])
    stats = await IS.whatsapp_frt_stats(db, since=now)
    assert stats["threads"] == 0 and stats["median_frt"] is None
    assert stats["unanswered"] == []


# ------------------------------------------------------------- worker tasks


class _LockRedis:
    def __init__(self, existing=None):
        self.store = {}
        self._existing = existing

    def get(self, k):
        return self._existing if self._existing is not None else self.store.get(k)

    def set(self, k, v, ex=None, nx=None):
        self.store[k] = v
        return True

    def delete(self, k):
        self.store.pop(k, None)


def test_funnel_task_dedupe_skip(monkeypatch):
    redis = _LockRedis(existing="1")
    monkeypatch.setattr(FT, "_dedupe_lock", lambda key: (redis, "k"))
    out = FT.run_lead_pulse(send=False)
    assert out == {"report": "lead_pulse", "skipped": "already-delivered-today"}


def test_funnel_task_runs_report_and_clears_lock_on_failure(monkeypatch):
    redis = _LockRedis()
    monkeypatch.setattr(FT, "_dedupe_lock", lambda key: (redis, "k"))
    monkeypatch.setattr(
        FT, "run_async",
        lambda coro: (coro.close(), {"report": "attribution", "emailed": False,
                      "email_error": "boom", "sections": 3})[1],
    )
    out = FT.run_attribution_digest(send=True)
    assert out["report"] == "attribution" and out["emailed"] is False
    assert "k" not in redis.store  # lock released because not emailed


def test_funnel_task_keeps_lock_when_emailed(monkeypatch):
    redis = _LockRedis()
    monkeypatch.setattr(FT, "_dedupe_lock", lambda key: (redis, "k"))
    monkeypatch.setattr(
        FT, "run_async",
        lambda coro: (coro.close(), {"report": "content_weekly", "emailed": True,
                      "email_error": None, "sections": 5})[1],
    )
    out = FT.run_content_weekly(send=True)
    assert out["emailed"] is True
    assert redis.store.get("k") == "1"


def test_funnel_task_no_redis_fallback(monkeypatch):
    monkeypatch.setattr(FT, "_dedupe_lock", lambda key: (None, None))
    monkeypatch.setattr(
        FT, "run_async",
        lambda coro: (coro.close(), {"report": "funnel_monthly", "emailed": False,
                      "email_error": None, "sections": 2})[1],
    )
    out = FT.run_funnel_monthly(send=False)
    assert out["report"] == "funnel_monthly"


@pytest.mark.asyncio
async def test_run_report_builds_and_sends(monkeypatch):
    report = FR.FunnelReport(key="lead_pulse", title="T", subject="s", preheader="p")
    report.line("S", "x")
    sent = FR.FunnelReport(key="lead_pulse", title="T", subject="s", preheader="p")
    sent.line("S", "y")
    sent.emailed = True
    import app.services.funnel_reports as svc

    monkeypatch.setattr(svc, "BUILDERS", {"lead_pulse": AsyncMock(return_value=report)})
    monkeypatch.setattr(svc, "_send", AsyncMock(return_value=sent))
    out = await FT._run_report("lead_pulse", send=True)
    assert out == {"report": "lead_pulse", "emailed": True,
                   "email_error": None, "sections": 1}


class _TeamResult:
    def scalars(self):
        return self

    def all(self):
        return [uuid.uuid4(), uuid.uuid4()]


@pytest.mark.asyncio
async def test_inbox_snapshot_task_runs_all_teams(monkeypatch):
    import app.db.session as dbs
    import app.services.inbox_snapshot as svc

    class _AM:
        async def __aenter__(self):
            return _FakeDB([_TeamResult()])

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(dbs, "async_session_maker", lambda: _AM())
    monkeypatch.setattr(svc, "capture", AsyncMock(return_value={"batch_id": "b", "conversations": 0, "by_platform": {}}))
    out = await IT._run()
    assert out["teams"] == 2 and len(out["results"]) == 2


@pytest.mark.asyncio
async def test_inbox_snapshot_task_isolates_team_failure(monkeypatch):
    import app.db.session as dbs
    import app.services.inbox_snapshot as svc

    class _AM:
        async def __aenter__(self):
            return _FakeDB([_TeamResult()])

        async def __aexit__(self, *a):
            return False

    async def _boom(db, team_id):
        raise RuntimeError("provider down")

    monkeypatch.setattr(dbs, "async_session_maker", lambda: _AM())
    monkeypatch.setattr(svc, "capture", _boom)
    out = await IT._run()
    assert out["teams"] == 2
    assert all("error" in r for r in out["results"])


# ---------------------------------------------------------------- build_* coverage


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def scalars(self):
        return self


class _Sess:
    """Fake async_session_maker: pops one canned result per execute()."""

    def __init__(self, queue):
        self._queue = queue

    async def execute(self, *a, **k):
        return _Q(self._queue.pop(0) if self._queue else [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_fr_db(monkeypatch, results):
    queue = list(results)
    monkeypatch.setattr(FR, "async_session_maker", lambda: _Sess(queue))


_INBOX = (
    {"whatsapp": {"total": 3, "unread": 2}, "messenger": {"total": 1, "unread": 0}},
    [{"platform": "whatsapp", "sender": "Sara", "preview": "hello?"}],
    {"replies_measured": 2, "median_frt": "10m", "p90_frt": "1h",
     "slowest_frt": "3h", "within_target": 2, "breaches_24h": 0,
     "unanswered": [{"name": "Kostas", "waiting_hours": 5}]},
)


@pytest.mark.asyncio
async def test_build_lead_pulse(monkeypatch):
    _patch_fr_db(monkeypatch, [[("linkedin", 3), (None, 2)]])
    monkeypatch.setattr(FR, "_social_leads_by_source",
                        AsyncMock(return_value={"linkedin": 2}))
    monkeypatch.setattr(FR, "_espo_count",
                        AsyncMock(side_effect=[{"web": 2}, {"proposal": 1}]))
    monkeypatch.setattr(FR, "_inbox_pulse", AsyncMock(return_value=_INBOX))
    r = await FR.build_lead_pulse()
    assert r.key == "lead_pulse"
    text = r.to_text()
    assert "SOCIALAUTO LEADS" in text and "linkedin: 2" in text
    assert "unread conversations" in text.lower() or "Unread" in text
    assert "Kostas" in text and "Sara" in text


@pytest.mark.asyncio
async def test_build_lead_pulse_espo_failure_and_empty_inbox(monkeypatch):
    _patch_fr_db(monkeypatch, [[]])
    monkeypatch.setattr(FR, "_social_leads_by_source", AsyncMock(return_value={}))
    monkeypatch.setattr(FR, "_espo_count",
                        AsyncMock(return_value={"(query failed)": -1}))
    monkeypatch.setattr(FR, "_inbox_pulse",
                        AsyncMock(return_value=({}, [], {"error": "provider down"})))
    r = await FR.build_lead_pulse()
    text = r.to_text()
    assert "query failed" in text
    assert "stats unavailable" in text


@pytest.mark.asyncio
async def test_build_attribution(monkeypatch):
    _patch_fr_db(monkeypatch, [
        [("https://www.google.com/x", 10), ("linkedin", 5), (None, 3)],
        [("cmp-a", 4), ("https://fb.com/y", 2)],
        [("linkedin", 2), (None, 1)],
    ])
    monkeypatch.setattr(FR, "_social_leads_by_source", AsyncMock(return_value={"x": 4}))
    monkeypatch.setattr(FR, "_espo_count",
                        AsyncMock(side_effect=[{"src": 3}, {"new": 1, "won": 2}]))
    r = await FR.build_attribution()
    assert r.key == "attribution"
    text = r.to_text()
    assert "google.com" in text and "BOOKINGS BY SOURCE" in text


@pytest.mark.asyncio
async def test_build_content_weekly(monkeypatch):
    sid1, sid2 = uuid.uuid4(), uuid.uuid4()
    _patch_fr_db(monkeypatch, [
        # current-week snapshots: post_id, platform, impressions, engagement,
        # reach, engagement_rate, content_text
        [("post-aaaa-1111", "linkedin", 100, 20, 80, 4.5, "hello world post"),
         ("post-bbbb-2222", "x", 30, 5, 25, 2.0, "low imp")],
        # previous-week snapshots
        [("post-aaaa-1111", "linkedin", 200, 30, 150, 3.0, "hello world post")],
        # latest follower snaps: account_id, followers, platform, username
        [(sid1, 100, "linkedin", "user1"), (sid2, 50, "x", "user2")],
        # older follower snaps: account_id, followers
        [(sid1, 90), (sid2, 50)],
    ])
    r = await FR.build_content_weekly()
    assert r.key == "content_weekly"
    text = r.to_text()
    assert "TOP POSTS" in text and "hello world post" in text
    assert "+10" in text  # follower delta for sid1
    assert "linkedin" in text


@pytest.mark.asyncio
async def test_build_funnel_monthly(monkeypatch):
    _patch_fr_db(monkeypatch, [[(12.5, 30, 500, 2)]])
    monkeypatch.setattr(FR, "_social_leads_by_source", AsyncMock(return_value={"li": 2}))
    monkeypatch.setattr(FR, "_espo_count",
                        AsyncMock(side_effect=[{"a": 1}, {"a": 2}, {"won": 1, "lost": 1}]))
    import app.services.polar_api as polar
    monkeypatch.setattr(polar, "polar_configured", lambda: False)
    r = await FR.build_funnel_monthly()
    assert r.key == "funnel_monthly"
    text = r.to_text()
    assert "Polar not configured" in text
    assert "€12.50" in text


@pytest.mark.asyncio
async def test_build_funnel_monthly_polar_orders(monkeypatch):
    _patch_fr_db(monkeypatch, [[(0.0, 0, 0, 0)]])
    monkeypatch.setattr(FR, "_social_leads_by_source", AsyncMock(return_value={}))
    monkeypatch.setattr(FR, "_espo_count", AsyncMock(return_value={}))
    import app.services.polar_api as polar
    monkeypatch.setattr(polar, "polar_configured", lambda: True)
    monkeypatch.setattr(polar, "_request", AsyncMock(
        return_value={"items": [{"status": "paid", "total_amount": 1999,
                                 "currency": "eur"}]}))
    r = await FR.build_funnel_monthly()
    assert "1 orders" in r.to_text() and "19.99" in r.to_text()


@pytest.mark.asyncio
async def test_build_funnel_monthly_polar_failure(monkeypatch):
    _patch_fr_db(monkeypatch, [[(0.0, 0, 0, 0)]])
    monkeypatch.setattr(FR, "_social_leads_by_source", AsyncMock(return_value={}))
    monkeypatch.setattr(FR, "_espo_count", AsyncMock(return_value={}))
    import app.services.polar_api as polar
    monkeypatch.setattr(polar, "polar_configured", lambda: True)
    monkeypatch.setattr(polar, "_request", AsyncMock(side_effect=RuntimeError("api down")))
    r = await FR.build_funnel_monthly()
    assert "query failed" in r.to_text()


# ---------------------------------------------------------------- task gaps


def test_dedupe_lock_returns_pair(monkeypatch):
    out = FT._dedupe_lock("lead_pulse")
    assert isinstance(out, tuple) and len(out) == 2


def test_execute_releases_lock_on_exception(monkeypatch):
    redis = _LockRedis()
    monkeypatch.setattr(FT, "_dedupe_lock", lambda key: (redis, "k"))

    def _boom(coro):
        coro.close()
        raise RuntimeError("worker died")

    monkeypatch.setattr(FT, "run_async", _boom)
    with pytest.raises(RuntimeError):
        FT.run_lead_pulse(send=False)
    assert "k" not in redis.store


# ---------------------------------------------------------------- helper coverage


@pytest.mark.asyncio
async def test_send_no_recipient_sets_error(monkeypatch):
    monkeypatch.setattr(FR, "get_settings",
                        lambda: SimpleNamespace(DIGEST_EMAIL_TO=""))
    r = FR.FunnelReport(key="k", title="T", subject="s", preheader="p")
    out = await FR._send(r)
    assert out.email_error == "DIGEST_EMAIL_TO not set"
    assert out.emailed is False


@pytest.mark.asyncio
async def test_send_delivers_and_marks_emailed(monkeypatch):
    monkeypatch.setattr(FR, "get_settings",
                        lambda: SimpleNamespace(DIGEST_EMAIL_TO="a@b.c, d@e.f"))
    import app.services.email_digest as ed
    sent = AsyncMock()
    monkeypatch.setattr(ed, "send_email", sent)
    r = FR.FunnelReport(key="k", title="T", subject="s", preheader="p")
    r.line("S", "body")
    out = await FR._send(r, email_to="override@x.y")
    assert out.emailed is True
    assert sent.await_args.kwargs["to_addrs"] == ["override@x.y"]
    assert sent.await_args.kwargs["category"] == "report"


@pytest.mark.asyncio
async def test_send_failure_records_error(monkeypatch):
    monkeypatch.setattr(FR, "get_settings",
                        lambda: SimpleNamespace(DIGEST_EMAIL_TO="a@b.c"))
    import app.services.email_digest as ed
    monkeypatch.setattr(ed, "send_email", AsyncMock(side_effect=RuntimeError("smtp down")))
    r = FR.FunnelReport(key="k", title="T", subject="s", preheader="p")
    out = await FR._send(r)
    assert out.emailed is False and "smtp down" in (out.email_error or "")


@pytest.mark.asyncio
async def test_espo_count_unconfigured_and_counts(monkeypatch):
    import app.services.espocrm_client as espo
    monkeypatch.setattr(espo, "configured", lambda: False)
    assert await FR._espo_count("Lead", since=datetime.now(UTC)) == {}

    monkeypatch.setattr(espo, "configured", lambda: True)
    monkeypatch.setattr(espo, "list_all", AsyncMock(return_value=[
        {"source": "web"}, {"source": "web"}, {"source": None}, {},
    ]))
    counts = await FR._espo_count("Lead", since=datetime.now(UTC),
                                  extra_where=[{"type": "equals", "attribute": "x", "value": 1}])
    assert counts == {"web": 2, "(unset)": 2}
    assert espo.list_all.await_args.kwargs["where"][1]["attribute"] == "x"


@pytest.mark.asyncio
async def test_espo_count_query_failure(monkeypatch):
    import app.services.espocrm_client as espo
    monkeypatch.setattr(espo, "configured", lambda: True)
    monkeypatch.setattr(espo, "list_all", AsyncMock(side_effect=RuntimeError("espo 500")))
    counts = await FR._espo_count("Lead", since=datetime.now(UTC))
    assert counts == {"(query failed)": -1}


@pytest.mark.asyncio
async def test_social_leads_by_source_enum_and_plain(monkeypatch):
    _patch_fr_db(monkeypatch, [[(SimpleNamespace(value="linkedin"), 2), ("web", 1), (None, 3)]])
    out = await FR._social_leads_by_source(datetime.now(UTC))
    assert out == {"linkedin": 2, "web": 1, "None": 3}


@pytest.mark.asyncio
async def test_inbox_pulse_aggregates_and_isolates_failures(monkeypatch):
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    _patch_fr_db(monkeypatch, [[t1, t2]])
    import app.services.inbox_snapshot as ISVC
    monkeypatch.setattr(ISVC, "capture", AsyncMock(side_effect=[None, RuntimeError("boom")]))
    summary = SimpleNamespace(
        by_platform={"whatsapp": {"total": 2, "unread": 1}},
        unread_conversations=[{"platform": "whatsapp", "sender": "A", "preview": "p"}],
    )
    monkeypatch.setattr(ISVC, "latest_summary", AsyncMock(return_value=summary))
    monkeypatch.setattr(ISVC, "whatsapp_frt_stats",
                        AsyncMock(return_value={"replies_measured": 1}))
    by_plat, unread, frt = await FR._inbox_pulse()
    assert by_plat == {"whatsapp": {"total": 2, "unread": 1}}
    assert unread and frt == {"replies_measured": 1}


@pytest.mark.asyncio
async def test_inbox_pulse_frt_failure(monkeypatch):
    _patch_fr_db(monkeypatch, [[]])
    import app.services.inbox_snapshot as ISVC
    monkeypatch.setattr(ISVC, "whatsapp_frt_stats",
                        AsyncMock(side_effect=RuntimeError("frt down")))
    by_plat, unread, frt = await FR._inbox_pulse()
    assert by_plat == {} and frt == {"error": "frt down"}
