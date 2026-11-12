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
    assert _task([]) == {"attached": 0, "failed": 0, "pending": 0, "scanned": 0}


def test_stale_post_fails():
    p = _post(created_at=datetime.now(UTC) - timedelta(hours=7))
    out = _task([p])
    assert out["failed"] == 1
    assert p.status.name == "FAILED" or str(p.status) == "PostStatus.FAILED"
    assert "pending_video_task" not in p.meta_data
    assert "timed out" in p.failure_reason


def test_naive_created_at_still_checked():
    p = _post(created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=7))
    out = _task([p])
    assert out["failed"] == 1


def test_result_not_ready_is_pending():
    p = _post()
    out = _task([p], res=_Res(ready=False))
    assert out["pending"] == 1


def test_failed_result_marks_failed():
    p = _post()
    out = _task([p], res=_Res(ready=True, ok=False, result="boom"))
    assert out["failed"] == 1
    assert "video render failed" in p.failure_reason


def test_bad_uuid_result_marks_failed():
    p = _post()
    out = _task([p], res=_Res(ready=True, ok=True, result="not-a-uuid"))
    assert out["failed"] == 1


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
