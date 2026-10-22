"""Unit tests for scripts/workforce_audit.py — earned-autonomy ladder.

Covers verdict persistence, streak-based graduation/demotion proposals,
and their surfacing in render()/push_slack().
"""
import uuid

import pytest

import scripts.workforce_audit as wa


class _Rows:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _DB:
    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return self._q.pop(0) if self._q else _Rows()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _report(statuses=("ok", "ok")):
    return {
        "workers": [
            {
                "name": "publisher",
                "status": s,
                "runs_seen": 3,
                "governance": {"autonomy": "auto"},
                "evidence": [],
                "escalations": [],
            }
            for s in statuses
        ]
    }


# ── _persist_verdicts ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_verdicts_writes_one_row_per_worker():
    db = _DB([_Rows(scalar=uuid.uuid4())])  # team lookup
    await wa._persist_verdicts(db, _report())
    assert db.commits == 1
    assert len(db.added) == 2
    assert all(o.action == "workforce-verdict" and o.resource_type == "worker" for o in db.added)
    assert db.added[0].meta["status"] == "ok"


@pytest.mark.asyncio
async def test_persist_verdicts_no_team_noop():
    db = _DB([_Rows(scalar=None)])
    await wa._persist_verdicts(db, _report())
    assert db.added == [] and db.commits == 0


# ── _autonomy_proposals ─────────────────────────────────────────────────


def _verdict_rows(name, statuses):
    return [(name, {"status": s}) for s in statuses]


@pytest.mark.asyncio
async def test_proposals_graduate_after_clean_streak():
    # content-factory sits at "review" — 10+ clean audits → propose "auto"
    rows = _verdict_rows("content-factory", ["ok"] * 12)
    db = _DB([_Rows(rows)])
    props = await wa._autonomy_proposals(db)
    assert len(props) == 1
    p = props[0]
    assert p["direction"] == "graduate" and p["worker"] == "content-factory"
    assert p["from"] == "review" and p["to"] == "auto"


@pytest.mark.asyncio
async def test_proposals_demote_auto_worker_on_recent_failure():
    rows = _verdict_rows("publisher", ["ok"] * 3 + ["dark"] + ["ok"] * 5)
    db = _DB([_Rows(rows)])
    props = await wa._autonomy_proposals(db)
    assert len(props) == 1
    p = props[0]
    assert p["direction"] == "demote" and p["from"] == "auto" and p["to"] == "review"


@pytest.mark.asyncio
async def test_proposals_noop_cases():
    # streak broken before threshold → nothing; non-auto worker with a
    # failure → no demote (demotion only narrows 'auto'); unknown worker skipped
    rows = (
        _verdict_rows("content-factory", ["ok"] * 5 + ["dark"] + ["ok"] * 9)
        + _verdict_rows("failure-learner", ["escalation"])
        + _verdict_rows("ghost-worker", ["ok"] * 20)
    )
    db = _DB([_Rows(rows)])
    props = await wa._autonomy_proposals(db)
    # content-factory: streak=9 < 10 → no grad. failure-learner is 'review'
    # → demotion doesn't apply to non-auto. ghost-worker not in roster.
    assert props == []


@pytest.mark.asyncio
async def test_proposals_auto_worker_stays_put():
    rows = _verdict_rows("publisher", ["ok"] * 30)
    db = _DB([_Rows(rows)])
    assert await wa._autonomy_proposals(db) == []  # already top tier


# ── render / push_slack surfacing ───────────────────────────────────────


def test_render_includes_proposals():
    report = {
        "generated_at": "2026-10-22T10:00:00",
        "celery_nodes": ["w1"],
        "celery_queues": ["default"],
        "inspect_ok": True,
        "workers": [
            {
                "name": "publisher", "runs_seen": 5, "status": "ok",
                "evidence": [], "escalations": [], "presence": True,
            }
        ],
        "autonomy_proposals": [
            {"worker": "voice-learner", "direction": "graduate", "from": "review", "to": "auto", "why": "10 clean"}
        ],
    }
    out = wa.render(report)
    assert "autonomy proposals" in out and "voice-learner" in out and "review → auto" in out


@pytest.mark.asyncio
async def test_push_slack_sends_proposals_when_all_clean(monkeypatch):
    sent = []

    async def _post(text, **kw):
        sent.append(text)

    monkeypatch.setattr(
        "app.services.slack_notifications.post_alert_to_slack", _post
    )
    report = {
        "workers": [
            {"name": "publisher", "status": "ok", "evidence": [], "escalations": []}
        ],
        "autonomy_proposals": [
            {"worker": "voice-learner", "direction": "graduate", "from": "review", "to": "auto", "why": "streak"}
        ],
    }
    await wa.push_slack(report)
    assert sent and "Autonomy ladder" in sent[0]


@pytest.mark.asyncio
async def test_push_slack_silent_when_nothing_to_say(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.slack_notifications.post_alert_to_slack",
        lambda text, **kw: sent.append(text),
    )
    await wa.push_slack({"workers": [{"name": "w", "status": "ok", "evidence": [], "escalations": []}]})
    assert sent == []
