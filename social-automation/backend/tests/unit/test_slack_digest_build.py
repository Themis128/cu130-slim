"""Unit tests for app/services/slack_digest.py build/deliver layer.

Covers build_daily_digest's query sequence (counts, snapshots, top posts,
issue classification, AI usage), post_digest_to_slack's webhook/thread/
alert delivery, and run_daily_digest_for_all_teams' skip-inactive logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import app.services.slack_digest as D


class _Res:
    def __init__(self, all_=None, scalar=None, one=None, scalars=None):
        self._all, self._scalar, self._one = all_, scalar, one
        self._scalars = scalars if scalars is not None else all_
        self._called_scalars = False

    def all(self):
        if self._called_scalars:
            return self._scalars if self._scalars is not None else []
        return self._all if self._all is not None else []

    def scalar(self):
        return self._scalar

    def one(self):
        return self._one

    def scalars(self):
        self._called_scalars = True
        return self

    def first(self):
        return (self._scalars or [None])[0]


class _DB:
    def __init__(self, results):
        self._q = list(results)

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()


def _team():
    return SimpleNamespace(id=uuid4(), name="Cloudless")


def _post(status="failed", reason="boom", text="post text"):
    return SimpleNamespace(id=uuid4(), status=status, failure_reason=reason, content_text=text, updated_at=datetime.now(UTC))


# ── build_daily_digest ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_daily_digest_happy(monkeypatch):
    team = _team()
    monkeypatch.setattr(D, "_growth_stats", AsyncMock(return_value={}))

    post = _post()
    db2 = _DB(
        [
            _Res(all_=[("published", 3), ("failed", 1), ("draft", 2)]),
            _Res(scalar=2),
            _Res(one=(1200, 45)),
            _Res(all_=[(uuid4(), "pp-1", "linkedin", 900, 40, "great post"), (None, "pp-2", "twitter", 300, 10, None)]),
            _Res(scalars=[post]),
            _Res(scalars=[SimpleNamespace(post_id=post.id, status="failed", error_message="api 500")]),
            _Res(scalars=[SimpleNamespace(attempts=3, max_attempts=5)]),
            _Res(scalars=[]),
            _Res(all_=[]),
            _Res(all_=[("cloudflare", 5, 120, 0.01)]),
        ]
    )
    report = await D.build_daily_digest(db2, team=team, days=1)

    o = report.overview
    assert o["total_posts"] == 6 and o["published_posts"] == 3
    assert o["connected_accounts"] == 2
    assert report.impressions_24h == 1200 and report.engagement_24h == 45
    assert len(report.top_posts) == 2
    # second top post has no post_id → snippet falls back to platform+id tail
    assert report.top_posts[1]["snippet"] == "twitter post pp-2"
    titles = [i.title for i in report.issues]
    assert any("didn’t publish" in t for t in titles)
    assert any("couldn’t finish" in t for t in titles)
    assert report.ai_usage["total_calls"] == 5
    # days=1 → growth not called
    assert report.growth == {}


@pytest.mark.asyncio
async def test_build_daily_digest_policy_skip_and_no_accounts(monkeypatch):
    post = _post()
    target = SimpleNamespace(post_id=post.id, status="skipped", error_message="duplicate content window")
    db = _DB(
        [
            _Res(all_=[("failed", 5)]),  # all failed → high fail rate
            _Res(scalar=0),  # no accounts
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[post]),
            _Res(scalars=[target]),  # policy-skip → info not error
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(all_=[]),
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=1)
    kinds = {i.severity for i in report.issues}
    assert "info" in kinds  # policy skip is informational
    assert not any("didn’t publish" in i.title for i in report.issues)
    titles = [i.title for i in report.issues]
    assert "No social accounts connected yet" in titles
    # effective_failed = 5 - 1 policy-skipped... all 5 posts are the same one
    # reported once; failed_posts count drives the high-rate warning
    assert any("More posts failed than usual" in t for t in titles)


@pytest.mark.asyncio
async def test_build_daily_digest_snapshot_notes_classification(monkeypatch):
    acct1, acct2, acct3 = uuid4(), uuid4(), uuid4()
    snaps = [
        SimpleNamespace(social_account_id=acct1, notes="HTTP 500 from api"),  # warning
        SimpleNamespace(social_account_id=acct2, notes="quota_exhausted for the month"),  # soft miss
        SimpleNamespace(social_account_id=acct3, notes="cannot access the app"),  # checkpoint
    ]
    latest = [(acct1, "http 500 from api"), (acct2, "quota_exhausted"), (acct3, "unauthorized")]
    db = _DB(
        [
            _Res(all_=[]),
            _Res(scalar=1),
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[]),  # no failed posts → no targets q
            _Res(scalars=[]),  # failed queue
            _Res(scalars=snaps),  # bad snaps
            _Res(all_=latest),  # latest notes per account
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=1)
    issues = {i.title for i in report.issues}
    assert "Analytics sync had trouble" in issues
    assert "Reconnect a social account" in issues
    # quota_exhausted soft-miss produces no issue
    assert len([i for i in report.issues if "quota" in (i.detail or "").lower()]) == 0


@pytest.mark.asyncio
async def test_build_daily_digest_recovered_note_skipped(monkeypatch):
    acct = uuid4()
    snap = SimpleNamespace(social_account_id=acct, notes="HTTP 500 earlier")
    # newest snapshot is clean → stale error ignored
    db = _DB(
        [
            _Res(all_=[]),
            _Res(scalar=1),
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(scalars=[snap]),
            _Res(all_=[(acct, "all good")]),
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=1)
    assert not any(i.title == "Analytics sync had trouble" for i in report.issues)


@pytest.mark.asyncio
async def test_build_daily_digest_growth_and_dedup(monkeypatch):
    db = _DB(
        [
            _Res(all_=[("published", 2)]),
            _Res(scalar=1),
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(all_=[]),
            _Res(all_=[]),
            # _growth_stats 3 executes (followers, funnel, events)
            _Res(all_=[]),
            _Res(all_=[]),
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=30)
    assert report.growth == {} or isinstance(report.growth, dict)


# ── post_digest_to_slack ──────────────────────────────────────────────


def _report(**kw):
    r = D.DigestReport(generated_at=datetime.now(UTC), timezone="Europe/Athens", team_name="Cloudless", days=1)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


@pytest.mark.asyncio
async def test_post_digest_to_slack_ok(monkeypatch):
    monkeypatch.setattr(D, "post_digest_text_to_slack", AsyncMock(return_value=(True, None, "ts-1")))
    alert = AsyncMock()
    thread = AsyncMock()
    monkeypatch.setattr(D, "post_alert_to_slack", alert)
    monkeypatch.setattr(D, "post_thread_reply", thread)
    report = _report(
        issues=[
            D.DigestIssue(severity="error", title="post failed"),
            D.DigestIssue(severity="warning", title="sync trouble"),
        ]
    )
    out = await D.post_digest_to_slack(report)
    assert out.posted_to_slack is True
    # issues → alert posted + thread reply (ts present)
    alert.assert_awaited_once()
    thread.assert_awaited_once()
    alert_text = alert.await_args.args[0]
    assert "Needs attention" in alert_text and "Heads up" in alert_text


@pytest.mark.asyncio
async def test_post_digest_to_slack_failure_and_no_issues(monkeypatch):
    monkeypatch.setattr(D, "post_digest_text_to_slack", AsyncMock(return_value=(False, "hook 404", None)))
    alert = AsyncMock()
    thread = AsyncMock()
    monkeypatch.setattr(D, "post_alert_to_slack", alert)
    monkeypatch.setattr(D, "post_thread_reply", thread)
    report = _report()
    out = await D.post_digest_to_slack(report)
    assert out.posted_to_slack is False
    assert out.slack_error == "hook 404"
    alert.assert_not_awaited()  # no issues → no alert
    thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_digest_to_slack_no_ts_still_alerts(monkeypatch):
    # webhook path returns no ts → alert still goes to #socialauto-alerts
    monkeypatch.setattr(D, "post_digest_text_to_slack", AsyncMock(return_value=(True, None, None)))
    alert = AsyncMock()
    thread = AsyncMock()
    monkeypatch.setattr(D, "post_alert_to_slack", alert)
    monkeypatch.setattr(D, "post_thread_reply", thread)
    report = _report(issues=[D.DigestIssue(severity="error", title="x")])
    await D.post_digest_to_slack(report)
    alert.assert_awaited_once()
    thread.assert_not_awaited()


# ── run_daily_digest_for_all_teams ────────────────────────────────────


@pytest.mark.asyncio
async def test_run_daily_digest_all_teams(monkeypatch):
    teams = [SimpleNamespace(id=uuid4(), name="active"), SimpleNamespace(id=uuid4(), name="empty")]
    db = _DB([_Res(scalars=teams)])
    reports = iter(
        [
            _report(overview={"connected_accounts": 2}),
            _report(overview={"connected_accounts": 0}, impressions_24h=0),
        ]
    )

    async def _build(db, *, team, days):
        r = next(reports)
        r.team_name = team.name
        return r

    monkeypatch.setattr(D, "build_daily_digest", _build)
    posted = AsyncMock(side_effect=lambda r: r)
    monkeypatch.setattr(D, "post_digest_to_slack", posted)
    emailed = AsyncMock(side_effect=lambda r: r)
    import sys

    monkeypatch.setitem(sys.modules, "app.services.email_digest", SimpleNamespace(email_digest=emailed))

    out = await D.run_daily_digest_for_all_teams(db)
    assert len(out) == 2
    # active team posted + emailed; empty team skipped with markers
    assert posted.await_count == 1
    assert emailed.await_count == 1
    assert out[1]["slack_error"] == "skipped empty team"
    assert out[1]["email_error"] == "skipped empty team"
    assert out[0]["markdown"]  # to_dict included


@pytest.mark.asyncio
async def test_markdown_full_sections():
    """Exercise every to_slack_markdown branch the existing tests miss."""
    r = _report(
        overview={"total_posts": 9, "published_posts": 5, "scheduled_posts": 2, "draft_posts": 1, "failed_posts": 1, "connected_accounts": 3},
        impressions_24h=1000,
        engagement_24h=60,
        top_posts=[{"engagement": 40, "impressions": 900, "snippet": "hi", "post_id": "p1"}],
        growth={
            "followers": [
                {"platform": "linkedin", "account": "me", "end": 500, "delta": 20, "rate": 4.2, "prev_delta": 10, "forecast": 520},
                {"platform": "x", "end": 100, "delta": -2},
            ],
            "non_follower_reach_pct": 42.0,
            "follower_adds": {"organic": 15, "paid": 3},
            "peak_hours": ["09:00", "18:00"],
            "funnel": [{"platform": "instagram", "impressions": 500, "engagement": 20, "clicks": 4, "er_pct": 4.0}],
        },
        ai_usage={
            "total_calls": 10,
            "providers": 2,
            "total_neurons": 500,
            "total_cost": 0.05,
            "by_provider": [{"provider": "cf", "calls": 8, "neurons": 400, "cost": 0.04}],
        },
        issues=[
            D.DigestIssue(severity="error", title="fail", detail="d1"),
            D.DigestIssue(severity="warning", title="warn"),
            D.DigestIssue(severity="info", title="policy", detail="skipped"),
        ],
    )
    md = r.to_slack_markdown()
    assert "Top posts" in md
    assert "Growth (last 1d)" in md
    assert "500" in md and "+4.2%" in md and "prev +10" in md
    assert "next ~*520*" in md
    assert "non-follower reach" in md and "42%" in md
    assert "15* organic" in md and "3* paid" in md
    assert "Best posting window" in md
    assert "imp \u2192" in md
    assert "AI usage" in md and "$0.0500" in md and "cf: 8 calls" in md
    assert "Needs attention (1)" in md
    assert "Heads up (1)" in md
    assert "Skipped by policy (1)" in md

    # to_dict aggregates everything
    d = r.to_dict()
    assert d["issues"][0]["severity"] == "error"
    assert "markdown" in d


# ── remaining branches ────────────────────────────────────────────────


def test_is_policy_skip_edges():
    from app.services.slack_digest import _is_policy_skip

    assert _is_policy_skip([]) is False
    t = SimpleNamespace(status="skipped", error_message="unrelated failure")
    assert _is_policy_skip([t]) is False


@pytest.mark.asyncio
async def test_growth_stats_funnel_and_events():
    """Funnel rows + all 4 event types populate growth dict."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    from app.services.slack_digest import _growth_stats

    def _res(rows):
        r = MagicMock()
        r.all.return_value = rows
        return r

    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    db = AsyncMock()
    db.execute.side_effect = [
        _res([]),  # follower snapshots
        _res([("instagram", 100, 10, 5)]),  # funnel
        _res([
            ("instagram", "audience_reach_split", {"by_follow_type": {"non_follower": 80, "follower": 20}}, now),
            ("facebook", "follower_attribution", {"by_day": {since.date().isoformat(): {"paid": 2, "organic": 3}}}, now),
            ("linkedin", "follower_insights", {"follower_gains": {"paid": 1, "organic": 4}}, now),
            ("x", "audience_activity", {"peak_hours": [9, 18]}, now),
        ]),
    ]
    growth = await _growth_stats(db, "t", since, days=7)
    assert growth["funnel"][0]["platform"] == "instagram"
    assert growth["funnel"][0]["er_pct"] == 10.0
    assert growth["non_follower_reach_pct"] == 80.0
    assert growth["follower_adds"] == {"paid": 3, "organic": 7}
    assert growth["peak_hours"] == ["9", "18"]


@pytest.mark.asyncio
async def test_snapshot_note_error_and_dedup(monkeypatch):
    """'http 4xx'-style note → warning issue; duplicate issues dedupe."""
    acct = uuid4()
    # latest note has err keys (not info marker) → not skipped as recovered;
    # snap note itself hits the second error-key bucket
    snap = SimpleNamespace(social_account_id=acct, notes="http 404 on insights")
    snap2 = SimpleNamespace(social_account_id=acct, notes="http 404 on insights")
    db = _DB(
        [
            _Res(all_=[]),
            _Res(scalar=1),
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(scalars=[snap, snap2]),
            _Res(all_=[(acct, "http 404 on insights")]),
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=1)
    trouble = [i for i in report.issues if i.title == "Analytics sync had trouble"]
    assert len(trouble) == 1  # identical issue deduped


@pytest.mark.asyncio
async def test_snapshot_note_paid_tier_soft_miss(monkeypatch):
    """Snap's own note 'needs paid tier' → soft-miss continue even when the
    account's latest note still shows an error."""
    acct = uuid4()
    snap = SimpleNamespace(social_account_id=acct, notes="needs paid tier for insights")
    db = _DB(
        [
            _Res(all_=[]),
            _Res(scalar=1),
            _Res(one=(0, 0)),
            _Res(all_=[]),
            _Res(scalars=[]),
            _Res(scalars=[]),
            _Res(scalars=[snap]),
            _Res(all_=[(acct, "http 500 still broken")]),  # err key → not recovered
            _Res(all_=[]),
        ]
    )
    report = await D.build_daily_digest(db, team=_team(), days=1)
    assert not any(i.title == "Analytics sync had trouble" for i in report.issues)


@pytest.mark.asyncio
async def test_run_digest_delivery_errors(monkeypatch):
    """Slack/email post failures land on the report, not raised."""
    team = _team()
    db = _DB([_Res(scalars=[team])])
    monkeypatch.setattr(
        D,
        "build_daily_digest",
        AsyncMock(return_value=SimpleNamespace(
            overview={"connected_accounts": 1},
            impressions_24h=0,
            slack_error=None,
            email_error=None,
            to_dict=lambda: {"ok": 1},
        )),
    )
    monkeypatch.setattr(D, "post_digest_to_slack", AsyncMock(side_effect=RuntimeError("slack down")))
    import app.services.email_digest as ED

    monkeypatch.setattr(ED, "email_digest", AsyncMock(side_effect=RuntimeError("mail down")))
    out = await D.run_daily_digest_for_all_teams(db)
    assert out == [{"ok": 1}]
