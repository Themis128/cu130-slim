"""Unit tests for edit_diff_learning — kept-vs-drafted deltas → voice suggestions."""
import uuid
from types import SimpleNamespace

import pytest

from app.services import edit_diff_learning as ed


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []

    async def execute(self, _stmt):
        return self._q.pop(0) if self._q else _Rows([])

    def add(self, obj):
        self.added.append(obj)


def _post_row(original, edited, days_ago=1):
    return (uuid.uuid4(), edited, {"original_text": original})


# ── diff_features ───────────────────────────────────────────────────────


def test_diff_features_all_flags():
    orig = "Same body.\n\nLine two. #old\n\nNo question here."
    edit = "New hook!\n\n" + "x" * 40 + "\n\nLine two. #new ✨\n\nWhat do you think?"
    f = ed.diff_features(orig, edit)
    assert f["hook_rewritten"] and f["lengthened"]
    assert f["emoji_added"] and f["hashtags_changed"]
    assert f["cta_added"] and f["paragraphs_added"]
    assert not f["shortened"] and not f["emoji_removed"] and not f["cta_removed"]


def test_diff_features_reverse_flags():
    orig = "Hook?\n\n" + "y" * 60 + " ✨✨\n\nCare to share? #a #b"
    edit = "Hook? short. #a"
    f = ed.diff_features(orig, edit)
    assert f["shortened"] and f["emoji_removed"]
    assert f["hashtags_changed"] and f["hook_rewritten"]  # first line differs
    assert not f["lengthened"] and not f["emoji_added"] and not f["cta_added"]


def test_diff_features_cta_removed():
    orig = "Some copy.\n\nWhat do you think?"
    edit = "Some copy.\n\nDone."
    f = ed.diff_features(orig, edit)
    assert f["cta_removed"] and not f["cta_added"]


def test_diff_features_empty_strings():
    f = ed.diff_features("", "hello")
    assert f["lengthened"] is True  # empty orig → any edit counts as longer
    f = ed.diff_features("hello", "")
    assert f["shortened"] is True


# ── distill_suggestions ─────────────────────────────────────────────────


def _diff_with(**flags):
    d = ed.EditDiff(str(uuid.uuid4()), {k: False for k in ed._SUGGESTIONS}, 0)
    d.features.update(flags)
    return d


def test_distill_empty_and_single_taste():
    assert ed.distill_suggestions([]) == []
    # one edit is taste, not a pattern
    assert ed.distill_suggestions([_diff_with(hook_rewritten=True)]) == []


def test_distill_pattern_threshold():
    diffs = [
        _diff_with(hook_rewritten=True),
        _diff_with(hook_rewritten=True),
        _diff_with(emoji_removed=True),
        _diff_with(),
        _diff_with(),
    ]
    out = ed.distill_suggestions(diffs)
    # 2/5 = 40% → hook pattern qualifies; emoji 1/5 does not
    assert any("Hook" in s for s in out)
    assert not any("emoji" in s for s in out)


# ── collect_edit_diffs ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_skips_unchanged_and_missing_baseline():
    rows = [
        _post_row("draft A", "edited A"),
        _post_row("same", "same"),          # baseline captured, edit never landed
        _post_row("", "edited"),            # empty baseline → unusable
        (uuid.uuid4(), "x", {"other": 1}),  # no original_text → defensive skip
    ]
    db = _DB([_Rows(rows)])
    diffs = await ed.collect_edit_diffs(db, uuid.uuid4())
    assert len(diffs) == 1  # only the real baseline→edit pair survives
    assert diffs[0].length_delta == len("edited A") - len("draft A")


# ── run_edit_diff_sweep ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_persists_artifact_and_slacks(monkeypatch):
    rows = [_post_row("o" * 100, "short")] * 3
    db = _DB([_Rows(rows)])
    slack = []
    async def _post(text, **kw):
        slack.append(text)

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _post)
    report = await ed.run_edit_diff_sweep(db, uuid.uuid4())
    assert report["edited_posts"] == 3
    assert report["suggestions"]  # 3/3 shortened → pattern
    assert any(o.action == "edit-diff" and o.resource_type == "sweep" for o in db.added)
    assert slack and "voice learning" in slack[0]


@pytest.mark.asyncio
async def test_sweep_no_suggestions_still_artifacts():
    db = _DB([_Rows([_post_row("a", "b")])])
    report = await ed.run_edit_diff_sweep(db, uuid.uuid4(), post_slack=False)
    assert report["suggestions"] == []
    assert [o.resource_type for o in db.added] == ["sweep"]


@pytest.mark.asyncio
async def test_sweep_slack_failure_still_reports(monkeypatch):
    rows = [_post_row("o" * 100, "short")] * 3
    db = _DB([_Rows(rows)])

    async def _boom(text, **kw):
        raise RuntimeError("slack down")

    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", _boom)
    report = await ed.run_edit_diff_sweep(db, uuid.uuid4())
    assert report["suggestions"]


# ── task wrapper ────────────────────────────────────────────────────────


def test_task_wrapper_happy_and_failure(monkeypatch):
    from app.worker.tasks import edit_diff as task_mod

    async def _run():
        return {"teams": 1}

    monkeypatch.setattr(task_mod, "_run", _run)
    assert task_mod.run_edit_diff_sweep() == {"teams": 1}

    async def _bad():
        raise RuntimeError("db gone")

    monkeypatch.setattr(task_mod, "_run", _bad)
    assert "error" in task_mod.run_edit_diff_sweep()


@pytest.mark.asyncio
async def test_task_inner_run_iterates_teams(monkeypatch):
    from app.worker.tasks import edit_diff as task_mod

    teams = [SimpleNamespace(id=uuid.uuid4())]

    class _Scalars(_Rows):
        def scalars(self):
            return self

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _q):
            return _Scalars(teams)

        async def commit(self):
            pass

    monkeypatch.setattr(task_mod, "get_session_factory", lambda: _Session)

    async def _fake_sweep(db, tid):
        return {"edited_posts": 4}

    monkeypatch.setattr(task_mod.edit_diff_learning, "run_edit_diff_sweep", _fake_sweep)
    out = await task_mod._run()
    assert out["teams"] == 1 and out["reports"] == [{"edited_posts": 4}]
