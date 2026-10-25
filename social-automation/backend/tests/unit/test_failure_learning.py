"""Unit tests for failure_learning — the recurring-signature → proposal loop.

Exercises the real service; only external boundaries are faked (the DB
result sets and the Slack HTTP post).
"""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import failure_learning as fl


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class _DB:
    """execute() returns canned results in call order; captures db.add()."""

    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []

    async def execute(self, _stmt):
        return self._q.pop(0) if self._q else _Rows([])

    def add(self, obj):
        self.added.append(obj)


def _fail_row(platform="tiktok", error="boom", hours_ago=2):
    return (platform, error, datetime.now(UTC) - timedelta(hours=hours_ago))


# ── classify_error ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error,expected",
    [
        ("url_ownership_unverified", "tiktok_domain_unverified"),
        ("TikTok spam_risk", "tiktok_spam_risk"),
        ("Session expired, please re-login", "session_expired"),
        ("media not available for upload", "instagram_media_missing"),
        ("X API 402 — usage-capped", "x_quota_exhausted"),
        ("(#200) permissions error", "meta_permissions"),
        ("object not found on Threads", "threads_object_not_found"),
        ("HTTP 429 too many requests", "rate_limited"),
        ("Unhandled exception while publishing", "unhandled_exception"),
        # real strings observed in post_targets.error_message
        ("TikTok publish failed: publish_cancelled", "tiktok_publish_cancelled"),
        ("Instagram requires at least one image. Set an image on the post.", "instagram_media_missing"),
        ("No public media URLs available for TikTok (MEDIA_PUBLIC_BASE_URL not set or Clou", "media_public_url_missing"),
        ("X free tier monthly write quota exhausted (1,500 tweets/month)", "x_quota_exhausted"),
        ("Facebook API error 403 (OAuthException) [code 200]: (#200) If posting to a group", "meta_permissions"),
        ("something totally novel", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_error(error, expected):
    sig_id, bucket, remediation = fl.classify_error(error)
    assert sig_id == expected
    assert bucket
    assert remediation


# ── collect_signatures ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_signatures_aggregates_and_samples():
    rows = [
        _fail_row("tiktok", "url_ownership_unverified"),
        _fail_row("tiktok", "url_ownership_unverified", 5),
        _fail_row("instagram", "Session expired"),
        _fail_row("instagram", "media not available"),
    ]
    db = _DB([_Rows(rows)])
    sigs = await fl.collect_signatures(db)
    assert sigs[("tiktok", "tiktok_domain_unverified")].count == 2
    assert sigs[("instagram", "session_expired")].count == 1
    assert sigs[("instagram", "instagram_media_missing")].count == 1
    # newest timestamp wins
    assert sigs[("tiktok", "tiktok_domain_unverified")].latest_at > datetime.now(UTC) - timedelta(hours=3)


@pytest.mark.asyncio
async def test_collect_signatures_tracks_newest_regardless_of_order():
    rows = [
        _fail_row("tiktok", "url_ownership_unverified", 50),  # older first
        _fail_row("tiktok", "url_ownership_unverified", 1),   # newer second
    ]
    db = _DB([_Rows(rows)])
    sigs = await fl.collect_signatures(db)
    sig = sigs[("tiktok", "tiktok_domain_unverified")]
    assert sig.count == 2
    assert sig.latest_at > datetime.now(UTC) - timedelta(hours=3)


@pytest.mark.asyncio
async def test_collect_signatures_empty_window():
    db = _DB([_Rows([])])
    assert await fl.collect_signatures(db) == {}


# ── run_failure_learning ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_emits_proposals_and_sweep_artifact(monkeypatch):
    rows = [_fail_row("tiktok", "url_ownership_unverified", h) for h in (1, 2, 3)]
    # call order: _recent_failures → dedup check (none) → done
    db = _DB([_Rows(rows), _Rows([])])
    slack = []
    async def _post(text, **kw):
        slack.append(text)

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _post)
    report = await fl.run_failure_learning(db, uuid.uuid4())
    assert report["scanned"] == 3
    assert report["recurring"] == 1
    assert len(report["proposals"]) == 1
    assert report["proposals"][0]["signature"] == "tiktok_domain_unverified"
    # proposal + sweep artifact persisted
    kinds = [o.resource_type for o in db.added]
    assert "signature" in kinds and "sweep" in kinds
    assert slack and "remediation proposals" in slack[0]


@pytest.mark.asyncio
async def test_run_dedupes_inside_cooldown(monkeypatch):
    rows = [_fail_row("tiktok", "url_ownership_unverified", h) for h in (1, 2, 3)]
    # dedup query returns a prior proposal → no new proposal
    db = _DB([_Rows(rows), _Rows([("exists",)])])
    slack = []
    async def _post(text, **kw):
        slack.append(text)

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _post)
    report = await fl.run_failure_learning(db, uuid.uuid4())
    assert report["proposals"] == []
    assert slack == []
    # sweep artifact still recorded
    assert any(o.resource_type == "sweep" for o in db.added)


@pytest.mark.asyncio
async def test_run_below_threshold_only_sweeps():
    rows = [_fail_row("tiktok", "url_ownership_unverified")]
    db = _DB([_Rows(rows)])
    report = await fl.run_failure_learning(db, uuid.uuid4(), post_slack=False)
    assert report["recurring"] == 0 and report["proposals"] == []
    assert [o.resource_type for o in db.added] == ["sweep"]


@pytest.mark.asyncio
async def test_run_slack_failure_still_reports(monkeypatch):
    rows = [_fail_row("instagram", "Session expired", h) for h in (1, 2, 3)]
    db = _DB([_Rows(rows), _Rows([])])

    async def _boom(text, **kw):
        raise RuntimeError("slack down")

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _boom)
    report = await fl.run_failure_learning(db, uuid.uuid4())
    assert len(report["proposals"]) == 1  # persisted even though Slack failed


@pytest.mark.asyncio
async def test_run_zero_failures_clean_sweep():
    db = _DB([_Rows([])])
    report = await fl.run_failure_learning(db, uuid.uuid4(), post_slack=False)
    assert report == {"scanned": 0, "signatures": 0, "recurring": 0, "proposals": [], "by_bucket": {}}


# ── task wrapper ────────────────────────────────────────────────────────


def test_task_wrapper_happy_and_failure(monkeypatch):
    from app.worker.tasks import failure_learning as task_mod

    async def _run():
        return {"teams": 1}

    monkeypatch.setattr(task_mod, "_run", _run)
    assert task_mod.run_failure_learning_sweep() == {"teams": 1}

    async def _bad():
        raise RuntimeError("db gone")

    monkeypatch.setattr(task_mod, "_run", _bad)
    assert "error" in task_mod.run_failure_learning_sweep()


@pytest.mark.asyncio
async def test_task_inner_run_iterates_teams(monkeypatch):
    from app.worker.tasks import failure_learning as task_mod

    teams = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]

    class _Scalars(_Rows):
        def scalars(self):
            return self

    class _Session:
        def __init__(self):
            self.commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _q):
            return _Scalars(teams)

        async def commit(self):
            self.commits += 1

    monkeypatch.setattr(task_mod, "get_session_factory", lambda: _Session)
    reports = []

    async def _fake_fl(db, tid):
        reports.append(tid)
        return {"ok": True}

    monkeypatch.setattr(task_mod.failure_learning, "run_failure_learning", _fake_fl)
    out = await task_mod._run()
    assert out["teams"] == 2 and len(out["reports"]) == 2
