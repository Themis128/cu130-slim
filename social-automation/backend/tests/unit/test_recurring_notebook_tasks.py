"""Tests for recurring-posts and notebook-report celery tasks.

DB sessions, notebook runner, and email sender are faked at boundaries;
the recurrence math, clone bookkeeping, and manifest delivery are real.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.content import RecurrencePattern
from app.worker.tasks import notebook_reports as NR
from app.worker.tasks import recurring as RC

# ── recurring._next_recurrence_dt ────────────────────────────────────


def test_next_recurrence_dt():
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert (RC._next_recurrence_dt(RecurrencePattern.DAILY, 2, base) - base).days == 2
    assert (RC._next_recurrence_dt(RecurrencePattern.WEEKLY, 1, base) - base).days == 7
    assert (RC._next_recurrence_dt(RecurrencePattern.MONTHLY, 1, base) - base).days == 30
    assert RC._next_recurrence_dt(RecurrencePattern.NONE, 5, base) == base


# ── recurring._process_recurring_posts_async ─────────────────────────


class _Res:
    def __init__(self, all_=None):
        self._all = all_ or []

    def scalars(self):
        return self

    def all(self):
        return self._all


class _DB:
    def __init__(self, posts, targets_by_post=None, fail_on=None):
        self.posts = posts
        self.targets_by_post = targets_by_post or {}
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self._q = list(posts)
        self._fail_on = fail_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        # First call = post query; subsequent = target queries per post
        if self._q is not None:
            posts, self._q = self._q, None
            return _Res(posts)
        return _Res(self.targets_by_post.pop(0) if self.targets_by_post else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # simulate id assignment
        for o in self.added:
            if getattr(o, "id", None) is None:
                try:
                    o.id = uuid.uuid4()
                except Exception:
                    pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _parent(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=None,
        content_text="hi",
        media_ids=["m1"],
        platform_specific={},
        hashtags=["a"],
        mention_accounts=[],
        link_url=None,
        music_asset_id=None,
        pillar_id=None,
        content_brief_id=None,
        is_recurring=True,
        recurrence_pattern=RecurrencePattern.WEEKLY,
        recurrence_interval=1,
        recurrence_count=0,
        recurrence_max=0,
        next_recurrence_at=datetime.now(UTC),
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_recurring_clones_due_post(monkeypatch):
    parent = _parent()
    target = SimpleNamespace(post_id=parent.id, social_account_id=uuid.uuid4())
    db = _DB([parent], targets_by_post=[[target]])
    monkeypatch.setattr(RC, "_worker_db", lambda: db)

    out = await RC._process_recurring_posts_async()
    assert out["cloned"] == 1 and out["skipped"] == 0 and not out["errors"]
    # clone + 1 target + 1 queue entry added
    assert len(db.added) == 3
    assert parent.recurrence_count == 1
    assert parent.next_recurrence_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_recurring_skips_maxed_post(monkeypatch):
    parent = _parent(recurrence_count=5, recurrence_max=5)
    db = _DB([parent])
    monkeypatch.setattr(RC, "_worker_db", lambda: db)

    out = await RC._process_recurring_posts_async()
    assert out["skipped"] == 1 and out["cloned"] == 0
    assert parent.is_recurring is False
    assert parent.next_recurrence_at is None
    assert not db.added  # nothing cloned


@pytest.mark.asyncio
async def test_recurring_error_rolls_back(monkeypatch):
    parent = _parent()

    class _ExplodingDB(_DB):
        async def commit(self):
            raise RuntimeError("db gone")

    db = _ExplodingDB([parent], targets_by_post=[[]])
    monkeypatch.setattr(RC, "_worker_db", lambda: db)

    out = await RC._process_recurring_posts_async()
    assert out["cloned"] == 0
    assert out["errors"] == [str(parent.id)]
    assert db.rollbacks == 1


def test_recurring_task_wrapper(monkeypatch):
    monkeypatch.setattr(RC, "run_async", lambda c: (c.close(), {"cloned": 1})[1])
    assert RC.process_recurring_posts() == {"cloned": 1}


# ── notebook_reports ────────────────────────────────────────────────


def _manifest(tmp_path: Path, **kw):
    html = tmp_path / "r.html"
    txt = tmp_path / "r.txt"
    html.write_text("<b>x</b>")
    txt.write_text("x")
    m = {"subject": "S", "html_file": "r.html", "text_file": "r.txt"}
    m.update(kw)
    return m, tmp_path


def test_notebook_report_success_send_off(monkeypatch, tmp_path):
    import app.services.notebook_runner as runner

    man, _ = _manifest(tmp_path)
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(man))
    monkeypatch.setattr(runner, "run_report_notebook", lambda nb, p: {"manifest": man, "manifest_path": str(mp), "duration_s": 1.5, "notebook": "nb.ipynb"})
    out = NR.run_notebook_report("nb", send=False)
    assert out["executed"] and out["emailed"] == 0 and out["duration_s"] == 1.5


def test_notebook_report_success_send_on(monkeypatch, tmp_path):
    import app.services.email_digest as ed
    import app.services.notebook_runner as runner

    man, _ = _manifest(tmp_path)
    mp = tmp_path / "manifest.json"
    mp.write_text("{}")
    monkeypatch.setattr(runner, "run_report_notebook", lambda nb, p: {"manifest": man, "manifest_path": str(mp), "duration_s": 1, "notebook": "n"})
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(ed, "send_email", sent)
    out = NR.run_notebook_report("nb", send=True)
    assert out["executed"] and out["emailed"] == 1
    sent.assert_awaited_once()


def test_notebook_report_multi_manifest(monkeypatch, tmp_path):
    import app.services.email_digest as ed
    import app.services.notebook_runner as runner

    (tmp_path / "a").mkdir(exist_ok=True)
    m1, _ = _manifest(tmp_path / "a")
    m2 = {"subject": "no files"}  # missing html/text → delivery error path
    mp = tmp_path / "m.json"
    mp.write_text("{}")
    monkeypatch.setattr(
        runner,
        "run_report_notebook",
        lambda nb, p: {"manifest": {"reports": [m1, m2]}, "manifest_path": str(tmp_path / "a" / "x"), "duration_s": 1, "notebook": "n"},
    )
    monkeypatch.setattr(ed, "send_email", AsyncMock(return_value=True))
    out = NR.run_notebook_report("nb", send=True)
    assert out["emailed"] == 1 and "missing html_file" in out["email_error"]


def test_notebook_report_failure_fallback(monkeypatch):
    import app.services.notebook_runner as runner

    monkeypatch.setattr(runner, "run_report_notebook", lambda nb, p: (_ for _ in ()).throw(RuntimeError("kernel died")))
    monkeypatch.setattr(NR, "run_async", lambda c: (c.close(), {"sent": 1})[1])
    out = NR.run_notebook_report("nb", fallback_to_code=True)
    assert out["executed"] is False and "kernel died" in out["error"]
    assert out["fallback"] == {"sent": 1}


def test_notebook_report_failure_no_fallback(monkeypatch):
    import app.services.notebook_runner as runner

    monkeypatch.setattr(runner, "run_report_notebook", lambda nb, p: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        NR.run_notebook_report("nb", fallback_to_code=False)


@pytest.mark.asyncio
async def test_deliver_manifest_send_error(monkeypatch, tmp_path):
    import app.services.email_digest as ed

    man, base = _manifest(tmp_path)
    monkeypatch.setattr(ed, "send_email", AsyncMock(side_effect=RuntimeError("smtp")))
    ok, err = await NR._deliver_manifest(man, base)
    assert ok is False and "smtp" in err


@pytest.mark.asyncio
async def test_deliver_manifest_attachments(monkeypatch, tmp_path):
    import app.services.email_digest as ed

    att = tmp_path / "chart.png"
    att.write_bytes(b"png")
    man, base = _manifest(tmp_path, attachments=[{"file": "chart.png", "cid": "c1", "mime": "image/png"}])
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(ed, "send_email", sent)
    ok, err = await NR._deliver_manifest(man, base)
    assert ok and err is None
    kwargs = sent.await_args.kwargs
    assert kwargs["attachments"][0]["data"] == b"png"
    assert kwargs["attachments"][0]["cid"] == "c1"


def test_notebook_fallback_mapping_uses_monetization_code_path():
    # A monetization notebook failure must fall back to the monetization
    # code path — never silently substitute a strategy brief.
    assert NR._CODE_FALLBACKS["monetization_daily"] is NR._fallback_monetization
    assert NR._CODE_FALLBACKS["daily_strategy_brief"] is NR._fallback_strategy


def test_notebook_report_monetization_fallback(monkeypatch):
    import app.services.notebook_runner as runner

    monkeypatch.setattr(runner, "run_report_notebook", lambda nb, p: (_ for _ in ()).throw(RuntimeError("kernel died")))
    seen = {}

    def _fake_run_async(coro):
        seen["coro"] = coro
        coro.close()
        return {"ok": True}

    monkeypatch.setattr(NR, "run_async", _fake_run_async)
    out = NR.run_notebook_report("monetization_daily", fallback_to_code=True)
    assert out["executed"] is False
    assert seen["coro"].cr_code == NR._fallback_monetization.__code__
