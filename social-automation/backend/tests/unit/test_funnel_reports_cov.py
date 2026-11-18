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
