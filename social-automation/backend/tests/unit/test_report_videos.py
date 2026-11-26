"""Coverage for materialize_report_videos — the render→attach materializer."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import app.worker.tasks.report_videos as RV


def _drain(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        s = MagicMock()
        s.all.return_value = self._rows
        return s


def _db(rows=None, get_map=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(return_value=_Exec(rows or []))
    db.get = AsyncMock(side_effect=lambda model, key: (get_map or {}).get(key))
    db.commit = AsyncMock()
    return db


def _session(*dbs):
    it = iter(dbs)
    last = dbs[-1]

    @asynccontextmanager
    async def _cm():
        try:
            yield next(it)
        except StopIteration:
            yield last

    return _cm


def _post(**kw):
    base = dict(
        id=uuid.uuid4(),
        status=None,
        failure_reason=None,
        meta_data={"pending_video_task": "task-1"},
        media_ids=[],
        scheduled_at=None,
        created_at=datetime.now(UTC),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Res:
    def __init__(self, ready=True, ok=True, result=None):
        self._ready, self._ok, self.result = ready, ok, result

    def ready(self):
        return self._ready

    def successful(self):
        return self._ok


def _task(rows, res=None, get_map=None, extra_db=None):
    """Run the task with rows in db1, res as AsyncResult, db2 from get_map."""
    shared = {r.id: r for r in rows}
    shared.update(get_map or {})
    dbs = [_db(rows=rows, get_map=shared)]
    if extra_db is not None:
        dbs.append(extra_db)
    else:
        dbs.append(_db(get_map=shared))
    cm = _session(*dbs)
    with (
        patch.object(RV, "run_async", _drain),
        patch.object(RV, "task_session", cm),
        patch("app.worker.celery_app.celery_app.AsyncResult", return_value=res),
    ):
        return RV.materialize_report_videos()


def test_no_pending_posts():
    assert _task([]) == {
        "attached": 0,
        "failed": 0,
        "retried": 0,
        "archived_stale_drafts": 0,
        "pending": 0,
        "scanned": 0,
    }


def test_stale_post_retries_as_infra_not_attempt():
    """A >6h undispatched result is a lost/wedged task — infra class, so it
    must not burn the genuine video_render_attempts budget."""
    p = _post(
        created_at=datetime.now(UTC) - timedelta(hours=7),
        meta_data={"pending_video_task": "task-1", "video_render_attempts": 2},
    )
    with patch.object(RV, "_redispatch_video", return_value="new-task-id"):
        out = _task([p])
    assert out["retried"] == 1 and out["failed"] == 0
    assert p.meta_data["video_render_attempts"] == 2
    assert p.meta_data["video_render_infra_retries"] == 1
    assert p.meta_data["pending_video_task"] == "new-task-id"


def test_naive_created_at_still_checked():
    p = _post(
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=7),
        meta_data={"pending_video_task": "task-1", "video_render_attempts": 2},
    )
    with patch.object(RV, "_redispatch_video", return_value="new-task-id"):
        out = _task([p])
    assert out["retried"] == 1


def test_result_not_ready_is_pending():
    p = _post()
    out = _task([p], res=_Res(ready=False))
    assert out["pending"] == 1


def test_failed_result_marks_failed():
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 2})
    out = _task([p], res=_Res(ready=True, ok=False, result="boom"))
    assert out["failed"] == 1
    assert "video render failed" in p.failure_reason


def test_bad_uuid_result_marks_failed():
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 2})
    out = _task([p], res=_Res(ready=True, ok=True, result="not-a-uuid"))
    assert out["failed"] == 1


def test_render_failure_retries_within_budget():
    p = _post()
    with patch.object(RV, "_redispatch_video", return_value="new-task-id") as rd:
        # _task patches run_async/task_session/AsyncResult; wrap again for rd
        shared = {p.id: p}
        cm = _session(_db(rows=[p], get_map=shared), _db(get_map=shared))
        with (
            patch.object(RV, "run_async", _drain),
            patch.object(RV, "task_session", cm),
            patch("app.worker.celery_app.celery_app.AsyncResult", return_value=_Res(ready=True, ok=False, result="boom")),
        ):
            out = RV.materialize_report_videos()
    assert out["retried"] == 1 and out["failed"] == 0
    rd.assert_called_once()
    assert p.meta_data["pending_video_task"] == "new-task-id"
    assert p.meta_data["video_render_attempts"] == 1
    assert "last_render_error" in p.meta_data


def test_render_failure_fails_after_budget():
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 2})
    out = _task([p], res=_Res(ready=True, ok=False, result="boom"))
    assert out["failed"] == 1 and out["retried"] == 0


def test_stale_machine_draft_archived():
    sp = _post(
        status="draft",
        meta_data={"source": "strategy-brief"},
        created_at=datetime.now(UTC) - timedelta(hours=80),
    )
    extra = _db(rows=[sp], get_map={sp.id: sp})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 1
    assert sp.status == "archived"
    assert "superseded" in (sp.failure_reason or "")


def test_fresh_machine_draft_not_archived():
    sp = _post(
        status="draft",
        meta_data={"source": "strategy-brief"},
        created_at=datetime.now(UTC) - timedelta(hours=10),
    )
    extra = _db(rows=[sp], get_map={sp.id: sp})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 0
    assert sp.status == "draft"


def test_manual_draft_not_archived():
    sp = _post(
        status="draft",
        meta_data={"source": "manual"},
        created_at=datetime.now(UTC) - timedelta(hours=200),
    )
    extra = _db(rows=[sp], get_map={sp.id: sp})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 0
    assert sp.status == "draft"


def test_missing_asset_fails():
    aid = uuid.uuid4()
    p = _post()
    out = _task([p], res=_Res(result=str(aid)), get_map={})
    assert out["failed"] == 1
    assert "asset missing" in p.failure_reason


def test_fresh_post_gone_is_skipped():
    aid = uuid.uuid4()
    p = _post()
    asset = SimpleNamespace(id=aid)
    out = _task([p], res=_Res(result=str(aid)), get_map={aid: asset, p.id: None})
    assert out["attached"] == 0 and out["failed"] == 0


def test_happy_path_attaches_and_schedules():
    aid = uuid.uuid4()
    p = _post()
    asset = SimpleNamespace(id=aid)
    fresh = _post(id=p.id)
    db2 = _db(get_map={aid: asset, p.id: fresh})
    out = _task([p], res=_Res(result=str(aid)), extra_db=db2)
    assert out["attached"] == 1
    assert fresh.media_ids == [aid]
    assert fresh.meta_data["video_asset_id"] == str(aid)
    assert "pending_video_task" not in fresh.meta_data
    assert fresh.scheduled_at is not None
    db2.commit.assert_awaited()


# ── _redispatch_video ───────────────────────────────────────────────────────


def test_redispatch_uses_meta_instruction():
    p = _post(meta_data={"pending_video_task": "t", "instruction": "  make a reel  "})
    p.team_id = uuid.uuid4()
    p.user_id = uuid.uuid4()
    with patch("app.worker.tasks.media.generate_video_asset_task") as g:
        g.delay.return_value = SimpleNamespace(id="task-9")
        assert RV._redispatch_video(p, p.meta_data) == "task-9"
    args = g.delay.call_args[0]
    assert args[2] == "make a reel"
    assert args[3] == {"model": "wan22"}


def test_redispatch_falls_back_to_content_text():
    p = _post(content_text="fallback prompt")
    p.meta_data = {"pending_video_task": "t"}
    p.team_id = uuid.uuid4()
    p.user_id = None
    with patch("app.worker.tasks.media.generate_video_asset_task") as g:
        g.delay.return_value = SimpleNamespace(id="task-8")
        assert RV._redispatch_video(p, p.meta_data) == "task-8"
    assert g.delay.call_args[0][2] == "fallback prompt"


def test_redispatch_empty_prompt_returns_none():
    p = _post(content_text="", meta_data={"pending_video_task": "t"})
    assert RV._redispatch_video(p, p.meta_data) is None


# ── _mark_failed / _retry_or_fail inner branches ────────────────────────────


def test_mark_failed_post_gone_is_noop():
    """fresh is None inside _mark_failed → return early, counter still ticks."""
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 2})
    cm = _session(_db(rows=[p], get_map={}), _db(get_map={}))
    with (
        patch.object(RV, "run_async", _drain),
        patch.object(RV, "task_session", cm),
        patch(
            "app.worker.celery_app.celery_app.AsyncResult",
            return_value=_Res(ready=True, ok=False, result="boom"),
        ),
    ):
        out = RV.materialize_report_videos()
    assert out["failed"] == 1
    assert p.status is None  # never marked — row vanished


def test_retry_dispatch_raises_falls_to_fail():
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 0})
    with patch.object(RV, "_redispatch_video", side_effect=RuntimeError("queue down")):
        out = _task([p], res=_Res(ready=True, ok=False, result="boom"))
    assert out["failed"] == 1 and out["retried"] == 0
    assert "video render failed" in p.failure_reason


def test_retry_post_gone_is_skipped():
    """fresh is None inside the retry-commit session → silent return."""
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 0})
    cm = _session(_db(rows=[p], get_map={p.id: p}), _db(get_map={}))
    with (
        patch.object(RV, "run_async", _drain),
        patch.object(RV, "task_session", cm),
        patch.object(RV, "_redispatch_video", return_value="new-task-id"),
        patch(
            "app.worker.celery_app.celery_app.AsyncResult",
            return_value=_Res(ready=True, ok=False, result="boom"),
        ),
    ):
        out = RV.materialize_report_videos()
    assert out["retried"] == 0 and out["failed"] == 0


# ── dispatched_at parsing ───────────────────────────────────────────────────


def test_dispatched_at_garbage_falls_back_to_created():
    p = _post(
        meta_data={
            "pending_video_task": "task-1",
            "video_render_attempts": 2,
            "video_render_dispatched_at": "not-a-date",
        }
    )
    out = _task([p], res=_Res(ready=False))
    assert out["pending"] == 1  # fresh created_at → not timed out


def test_dispatched_at_naive_old_is_timed_out():
    naive_old = (datetime.now(UTC) - timedelta(hours=7)).replace(tzinfo=None)
    p = _post(
        meta_data={
            "pending_video_task": "task-1",
            "video_render_attempts": 2,
            "video_render_dispatched_at": naive_old.isoformat(),
        }
    )
    with patch.object(RV, "_redispatch_video", return_value="new-task-id"):
        out = _task([p])
    assert out["retried"] == 1  # infra-class stale dispatch → free retry
    assert p.meta_data["video_render_infra_retries"] == 1


def test_infra_budget_exhausted_fails():
    p = _post(
        meta_data={
            "pending_video_task": "task-1",
            "video_render_attempts": 2,
            "video_render_infra_retries": RV._MAX_INFRA_RETRIES,
        }
    )
    out = _task([p], res=_Res(ready=True, ok=False, result="gpu media lock timeout"))
    assert out["failed"] == 1 and out["retried"] == 0
    assert "gpu media lock" in p.failure_reason


def test_infra_failure_does_not_burn_attempts():
    p = _post(meta_data={"pending_video_task": "task-1", "video_render_attempts": 2})
    with patch.object(RV, "_redispatch_video", return_value="new-task-id"):
        cm = _session(_db(rows=[p], get_map={p.id: p}), _db(get_map={p.id: p}))
        with (
            patch.object(RV, "run_async", _drain),
            patch.object(RV, "task_session", cm),
            patch(
                "app.worker.celery_app.celery_app.AsyncResult",
                return_value=_Res(ready=True, ok=False, result="gpu media lock not acquired"),
            ),
        ):
            out = RV.materialize_report_videos()
    assert out["retried"] == 1 and out["failed"] == 0
    assert p.meta_data["video_render_attempts"] == 2  # untouched
    assert p.meta_data["video_render_infra_retries"] == 1


def test_is_infra_reason_classification():
    assert RV._is_infra_reason("video render timed out (>6h)")
    assert RV._is_infra_reason("gpu media lock not acquired within 600s")
    assert RV._is_infra_reason("database system is starting up")
    assert RV._is_infra_reason("ComfyUI unreachable: connection refused")
    assert not RV._is_infra_reason("prompt rejected: invalid parameters")
    assert not RV._is_infra_reason("boom")


# ── happy-path scheduled_at already set ─────────────────────────────────────


def test_happy_path_keeps_existing_scheduled_at():
    aid = uuid.uuid4()
    when = datetime.now(UTC) + timedelta(hours=5)
    p = _post()
    asset = SimpleNamespace(id=aid)
    fresh = _post(id=p.id, scheduled_at=when)
    db2 = _db(get_map={aid: asset, p.id: fresh})
    out = _task([p], res=_Res(result=str(aid)), extra_db=db2)
    assert out["attached"] == 1
    assert fresh.scheduled_at == when


# ── stale-archive inner branches ────────────────────────────────────────────


def test_stale_draft_naive_created_at_archived():
    naive_old = (datetime.now(UTC) - timedelta(hours=80)).replace(tzinfo=None)
    sp = _post(
        status="draft",
        meta_data={"source": "monetization-report"},
        created_at=naive_old,
    )
    extra = _db(rows=[sp], get_map={sp.id: sp})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 1


def test_stale_draft_no_longer_draft_skipped():
    sp = _post(
        status="draft",
        meta_data={"source": "strategy-brief"},
        created_at=datetime.now(UTC) - timedelta(hours=80),
    )
    moved = _post(id=sp.id, status="scheduled", meta_data={"source": "strategy-brief"})
    extra = _db(rows=[sp], get_map={sp.id: moved})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 0
    assert moved.status == "scheduled"


def test_stale_draft_render_in_flight_skipped():
    sp = _post(
        status="draft",
        meta_data={"source": "strategy-brief"},
        created_at=datetime.now(UTC) - timedelta(hours=80),
    )
    busy = _post(
        id=sp.id,
        status="draft",
        meta_data={"source": "strategy-brief", "pending_video_task": "task-x"},
    )
    extra = _db(rows=[sp], get_map={sp.id: busy})
    out = _task([], extra_db=extra)
    assert out["archived_stale_drafts"] == 0
    assert busy.status == "draft"


def test_dispatched_at_aware_recent_is_pending():
    aware = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    p = _post(
        meta_data={
            "pending_video_task": "task-1",
            "video_render_attempts": 2,
            "video_render_dispatched_at": aware,
        }
    )
    out = _task([p], res=_Res(ready=False))
    assert out["pending"] == 1
