"""Tests for app/worker/tasks/publishing.py — the Celery publish queue worker."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.worker.tasks.publishing as W
from app.models.content import PostStatus
from app.models.queue import PublishQueue, QueueStatus


def _res(*, scalars_all=None, one=None, first=None, rowcount=None, all_=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            all=lambda: scalars_all or [],
            first=lambda: first,
        ),
        scalar_one_or_none=lambda: one,
        all=lambda: all_ or [],
        rowcount=rowcount,
    )


class _FakeDB:
    """Executes return queued _res objects; scalar() returns queued values."""

    def __init__(self, results=None, scalars=None):
        self._results = deque(results or [])
        self._scalars = deque(scalars or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, q):
        return self._results.popleft()

    async def scalar(self, q):
        return self._scalars.popleft()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def rollback(self):
        self.rollbacks += 1


class _DBCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _qitem(**kw):
    d = dict(
        id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        social_account_id=uuid.uuid4(),
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        priority=5,
        status=QueueStatus.PENDING,
        locked_at=None,
        locked_by=None,
        attempts=0,
        max_attempts=3,
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _post(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        content_text="hi there",
        media_ids=[],
        platform_specific={},
        meta_data={},
        is_recurring=False,
        scheduled_at=None,
        status=PostStatus.SCHEDULED,
        published_at=None,
        failed_at=None,
        failure_reason=None,
        updated_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _acct(**kw):
    d = dict(
        id=uuid.uuid4(),
        platform="twitter",
        account_id="u1",
        account_type="user",
        username="cu_dev",
        meta_data={},
        access_token_enc=b"enc",
        team_id=uuid.uuid4(),
        status="active",
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _target(**kw):
    d = dict(
        id=uuid.uuid4(),
        post_id=None,
        social_account_id=None,
        status="pending",
        platform_post_id=None,
        platform_url=None,
        published_at=None,
        error_message=None,
    )
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture(autouse=True)
def _patch_edges(monkeypatch):
    monkeypatch.setattr(W, "_rollup_post_status", AsyncMock())
    monkeypatch.setattr(W, "_notify_publish_failure", AsyncMock())
    monkeypatch.setattr(W, "_notify_publish_success", AsyncMock())
    monkeypatch.setattr(W, "_maybe_send_publish_summary", AsyncMock())
    monkeypatch.setattr(W, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(W, "flag_modified", lambda *a, **kw: None)
    monkeypatch.setattr(W, "media_duplicate_reason", AsyncMock(return_value=None))


def _patch_db(monkeypatch, db):
    monkeypatch.setattr(W, "_worker_db", lambda: _DBCtx(db))


# ── pure helpers ──────────────────────────────────────────────────────


def test_aware():
    assert W._aware(None) is None
    naive = datetime(2025, 1, 1)
    assert W._aware(naive).tzinfo is UTC
    aware = datetime(2025, 1, 1, tzinfo=UTC)
    assert W._aware(aware) is aware


def test_is_x_capacity_error():
    assert W._is_x_capacity_error("API credits exhausted")
    assert W._is_x_capacity_error("circuit breaker open")
    assert not W._is_x_capacity_error("deferral limit reached: credits")
    assert not W._is_x_capacity_error("duplicate content credits")
    assert not W._is_x_capacity_error(None)
    assert not W._is_x_capacity_error("random failure")


def test_content_preview():
    post = _post(content_text="x" * 200)
    assert len(W._content_preview(post, limit=140)) <= 141
    assert W._content_preview(_post(content_text=None)) == ""


def test_compute_post_rollup():
    F = W._compute_post_rollup
    assert F(target_statuses=[], in_flight=False, failure_reason=None)[0] == PostStatus.FAILED
    st, partial, _ = F(target_statuses=["published", "failed"], in_flight=False, failure_reason="x")
    assert st == PostStatus.PUBLISHED and partial is True
    st, partial, _ = F(target_statuses=["published", "published"], in_flight=False, failure_reason=None)
    assert st == PostStatus.PUBLISHED and partial is False
    assert F(target_statuses=["pending"], in_flight=False, failure_reason=None)[0] == PostStatus.PUBLISHING
    assert F(target_statuses=["published"], in_flight=True, failure_reason=None)[0] == PostStatus.PUBLISHING
    st, _, reason = F(target_statuses=["skipped", "skipped"], in_flight=False, failure_reason=None)
    assert st == PostStatus.FAILED and "skipped" in reason
    st, _, reason = F(target_statuses=["failed"], in_flight=False, failure_reason="boom")
    assert st == PostStatus.FAILED and reason == "boom"


def test_apply_capacity_deferral_deferred(monkeypatch):
    item = _qitem()
    post = _post()
    target = _target()
    now = datetime.now(UTC)
    monkeypatch.setattr(W, "flag_modified", lambda *a, **kw: None)
    r = W._apply_capacity_deferral(
        item=item,
        target=target,
        post=post,
        account_id=item.social_account_id,
        retry_after=now + timedelta(hours=2),
        error="cap",
        now=now,
        max_hours=72.0,
        max_count=500,
    )
    assert r is None  # deferred
    assert item.status == QueueStatus.PENDING
    assert item.scheduled_at == now + timedelta(hours=2)
    assert item.locked_at is None
    assert target.status == "pending" and "Deferred #1" in target.error_message
    key = str(item.social_account_id)
    state = post.platform_specific[W._DEFER_STATE_KEY][key]
    assert state["count"] == 1


def test_apply_capacity_deferral_gives_up():
    old = datetime.now(UTC) - timedelta(hours=100)
    item = _qitem(created_at=old)
    post = _post()
    r = W._apply_capacity_deferral(
        item=item,
        target=None,
        post=post,
        account_id=item.social_account_id,
        retry_after=datetime.now(UTC) + timedelta(hours=1),
        error="cap",
        now=datetime.now(UTC),
        max_hours=72.0,
        max_count=500,
    )
    assert r is not None and "gave up" in r


def test_clear_deferral_state():
    aid = uuid.uuid4()
    post = _post(platform_specific={W._DEFER_STATE_KEY: {str(aid): {"count": 2}}})
    W._clear_deferral_state(post, aid)
    assert W._DEFER_STATE_KEY not in (post.platform_specific or {})
    # unrelated state survives
    other = uuid.uuid4()
    post2 = _post(platform_specific={W._DEFER_STATE_KEY: {str(other): {}}})
    W._clear_deferral_state(post2, aid)
    assert str(other) in post2.platform_specific[W._DEFER_STATE_KEY]


# ── process_publish_queue_async ───────────────────────────────────────


@pytest.mark.asyncio
async def test_pq_empty(monkeypatch):
    db = _FakeDB([_res(scalars_all=[])])
    _patch_db(monkeypatch, db)
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_pq_collapses_duplicates(monkeypatch):
    pid, aid = uuid.uuid4(), uuid.uuid4()
    items = [_qitem(post_id=pid, social_account_id=aid), _qitem(post_id=pid, social_account_id=aid)]
    post = _post(id=pid)
    acct = _acct(id=aid)
    target = _target(post_id=pid, social_account_id=aid)
    db = _FakeDB(
        [
            _res(scalars_all=items),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),  # recent texts
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=True, platform_post_id="x1")))
    await W._process_publish_queue_async()
    assert items[1].status == QueueStatus.COMPLETED  # dupe collapsed
    assert items[0].status == QueueStatus.COMPLETED
    assert target.status == "published"


@pytest.mark.asyncio
async def test_pq_post_and_account_missing(monkeypatch):
    item = _qitem()
    db = _FakeDB([_res(scalars_all=[item]), _res(one=None)])
    _patch_db(monkeypatch, db)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    W._notify_publish_failure.assert_awaited()

    W._notify_publish_failure.reset_mock()
    item2 = _qitem()
    db2 = _FakeDB(
        [
            _res(scalars_all=[item2]),
            _res(one=_post(id=item2.post_id)),
            _res(one=None),
        ]
    )
    _patch_db(monkeypatch, db2)
    await W._process_publish_queue_async()
    assert item2.status == QueueStatus.FAILED
    W._notify_publish_failure.assert_awaited()


@pytest.mark.asyncio
async def test_pq_idempotent_published_target(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    target = _target(status="published")
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
        ]
    )
    _patch_db(monkeypatch, db)
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    pub.assert_not_awaited()  # never republish


@pytest.mark.asyncio
async def test_pq_duplicate_text_skipped(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id, content_text="same copy")
    acct = _acct(id=item.social_account_id)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=["same copy"]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "is_duplicate", lambda a, b, threshold=0.9: True)
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    assert target.status == "skipped" and "identical content" in target.error_message
    pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_pq_media_dup_skipped(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "media_duplicate_reason", AsyncMock(return_value="same image set"))
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert target.status == "skipped" and target.error_message == "same image set"
    pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_pq_happy_publish_merges_meta(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(
        W,
        "publish_to_platform",
        AsyncMock(
            return_value=W.PublishResult(
                success=True, platform_post_id="pid-1", platform_url="https://x/1", platform_meta={"tiktok": {"publish_id": "p9"}, "flat": 1}
            )
        ),
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    assert target.status == "published" and target.platform_post_id == "pid-1"
    assert post.platform_specific["tiktok"]["publish_id"] == "p9"
    assert post.platform_specific["flat"] == 1
    W._notify_publish_success.assert_awaited_once()
    W._maybe_send_publish_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_pq_skipped_result(monkeypatch):
    item = _qitem()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=_post(id=item.post_id)),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=_target()),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, skipped=True, error="no media")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED


@pytest.mark.asyncio
async def test_pq_failure_requeues_then_fails(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, error="transient")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.PENDING and item.attempts == 1
    assert item.locked_at is None

    # final attempt → FAILED + notify
    item2 = _qitem(attempts=2, max_attempts=3)
    target2 = _target(error_message="previous")
    db2 = _FakeDB(
        [
            _res(scalars_all=[item2]),
            _res(one=post),
            _res(one=_acct(id=item2.social_account_id)),
            _res(one=target2),
            _res(scalars_all=[]),
            _res(scalars_all=[]),  # _target_status_line
        ]
    )
    _patch_db(monkeypatch, db2)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="1/1 failed"))
    await W._process_publish_queue_async()
    assert item2.status == QueueStatus.FAILED and item2.attempts == 3
    assert target2.status == "failed" and target2.error_message == "transient"
    W._notify_publish_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_pq_permanent_jumps_attempts(monkeypatch):
    item = _qitem(attempts=0, max_attempts=3)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=_post(id=item.post_id)),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, permanent=True, error="bad media")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED  # attempts jumped to max


@pytest.mark.asyncio
async def test_pq_retry_after_defers(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    later = datetime.now(UTC) + timedelta(hours=3)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, error="cap", retry_after=later)))
    monkeypatch.setattr(W, "get_settings", lambda: SimpleNamespace(PUBLISH_DEFER_MAX_HOURS=72.0, PUBLISH_DEFER_MAX_COUNT=500))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.PENDING and item.scheduled_at == later
    assert target.status == "pending"
    assert W._DEFER_STATE_KEY in post.platform_specific


@pytest.mark.asyncio
async def test_pq_deferral_gave_up_is_permanent(monkeypatch):
    old = datetime.now(UTC) - timedelta(hours=100)
    item = _qitem(created_at=old)
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=_post(id=item.post_id)),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, error="cap", retry_after=datetime.now(UTC))))
    monkeypatch.setattr(W, "get_settings", lambda: SimpleNamespace(PUBLISH_DEFER_MAX_HOURS=72.0, PUBLISH_DEFER_MAX_COUNT=500))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    assert target.status == "failed" and "gave up" in target.error_message


@pytest.mark.asyncio
async def test_pq_ambiguous_ig_schedules_reconcile(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    acct = _acct(platform="instagram")
    target = _target()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=_post(id=item.post_id)),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=False, error="limit", ambiguous=True)))
    sched = Mock()
    monkeypatch.setattr(W.reconcile_instagram_publish, "apply_async", sched)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    sched.assert_called_once()
    assert sched.call_args.kwargs["countdown"] == 300


@pytest.mark.asyncio
async def test_pq_exception_path(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    target = _target(error_message="old")
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),  # recent texts (dup check)
            # error-path re-fetch:
            _res(one=post),
            _res(one=acct),
            _res(one=target),
            _res(scalars_all=[]),  # _target_status_line
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("kaboom")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    assert db.rollbacks == 1
    assert target.status == "failed"
    assert "Unhandled exception" in target.error_message
    W._notify_publish_failure.assert_awaited_once()


# ── check_scheduled_posts / publish_post_now / cleanup / sweep ────────


@pytest.mark.asyncio
async def test_scheduled_no_targets_fails(monkeypatch):
    post = _post(status=PostStatus.SCHEDULED)
    db = _FakeDB(
        [
            _res(scalars_all=[post]),
            _res(scalars_all=[]),
        ]
    )
    _patch_db(monkeypatch, db)
    await W._check_scheduled_posts_async()
    assert post.status == PostStatus.FAILED
    assert "No target accounts" in post.failure_reason


@pytest.mark.asyncio
async def test_scheduled_creates_queue_rows(monkeypatch):
    post = _post(status=PostStatus.SCHEDULED)
    target = _target(social_account_id=uuid.uuid4())
    db = _FakeDB(
        [
            _res(scalars_all=[post]),
            _res(scalars_all=[target]),
            _res(one=None),  # no existing queue row
        ]
    )
    _patch_db(monkeypatch, db)
    await W._check_scheduled_posts_async()
    assert post.status == PostStatus.PUBLISHING
    assert len(db.added) == 1 and isinstance(db.added[0], PublishQueue)
    assert db.added[0].priority == 5


@pytest.mark.asyncio
async def test_scheduled_existing_row_not_duplicated(monkeypatch):
    post = _post(status=PostStatus.SCHEDULED)
    target = _target(social_account_id=uuid.uuid4())
    db = _FakeDB(
        [
            _res(scalars_all=[post]),
            _res(scalars_all=[target]),
            _res(one=object()),  # existing pending row
        ]
    )
    _patch_db(monkeypatch, db)
    await W._check_scheduled_posts_async()
    assert not db.added


@pytest.mark.asyncio
async def test_publish_post_now_paths(monkeypatch):
    db = _FakeDB([_res(one=None)])
    _patch_db(monkeypatch, db)
    r = await W._publish_post_now_async("p1", ["a1"])
    assert r["success"] is False

    post = _post()
    aid = uuid.uuid4()
    db2 = _FakeDB(
        [
            _res(one=post),
            _res(one=None),  # account a-miss
            _res(one=_acct(id=aid)),  # account found
            _res(one=None),  # no existing row
        ]
    )
    _patch_db(monkeypatch, db2)
    r2 = await W._publish_post_now_async("p1", ["a-miss", str(aid)])
    assert r2["success"] is True
    assert r2["results"][0]["success"] is False
    assert r2["results"][1]["queued"] is True
    assert post.status == PostStatus.PUBLISHING


@pytest.mark.asyncio
async def test_cleanup_queue(monkeypatch):
    db = _FakeDB([_res(rowcount=7)])
    _patch_db(monkeypatch, db)
    r = await W._cleanup_publish_queue_async(3)
    assert r["deleted"] == 7


@pytest.mark.asyncio
async def test_requeue_sweep(monkeypatch):
    now = datetime.now(UTC)
    acct = _acct(platform="twitter")
    post = _post(status=PostStatus.FAILED, media_ids=[])
    target = _target(status="failed", error_message="API credits exhausted", platform_post_id=None)
    db = _FakeDB(
        results=[_res(all_=[(target, post, acct)])],
        scalars=[0, now],  # no active row, recent last_row
    )
    _patch_db(monkeypatch, db)
    r = await W._requeue_capacity_stuck_x_async()
    assert r["requeued"] == [str(post.id)]
    assert target.status == "pending"
    assert isinstance(db.added[0], PublishQueue)


@pytest.mark.asyncio
async def test_requeue_sweep_skips(monkeypatch):
    now = datetime.now(UTC)
    acct = _acct(platform="twitter")
    post = _post(status=PostStatus.FAILED, media_ids=[])
    # non-capacity error → not re-queued
    t_bad = _target(status="failed", error_message="syntax error")
    # already swept → not re-queued
    aid = acct.id
    post2 = _post(status=PostStatus.FAILED, media_ids=[], platform_specific={W._SWEEP_STATE_KEY: {str(aid): {"at": "x"}}})
    t_swept = _target(status="failed", error_message="credits depleted")
    db = _FakeDB(results=[_res(all_=[(t_bad, post, acct), (t_swept, post2, acct)])])
    _patch_db(monkeypatch, db)
    r = await W._requeue_capacity_stuck_x_async()
    assert r["requeued"] == []

    # active queue row → skip; old last_row → skip; missing media → skip
    post3 = _post(status=PostStatus.FAILED, media_ids=[uuid.uuid4()])
    t3 = _target(status="failed", error_message="daily cap reached")
    db2 = _FakeDB(
        results=[_res(all_=[(t3, post3, acct)])],
        scalars=[1],  # active row exists → skip early
    )
    _patch_db(monkeypatch, db2)
    r2 = await W._requeue_capacity_stuck_x_async()
    assert r2["requeued"] == []

    db3 = _FakeDB(
        results=[_res(all_=[(t3, post3, acct)])],
        scalars=[0, now - timedelta(hours=100)],  # last row too old
    )
    _patch_db(monkeypatch, db3)
    r3 = await W._requeue_capacity_stuck_x_async()
    assert r3["requeued"] == []

    db4 = _FakeDB(
        results=[_res(all_=[(t3, post3, acct)])],
        scalars=[0, now, 0],  # media count mismatch (want 1, found 0)
    )
    _patch_db(monkeypatch, db4)
    r4 = await W._requeue_capacity_stuck_x_async()
    assert r4["requeued"] == []


# ── instagram reconcile ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_paths(monkeypatch):
    # post not found
    db = _FakeDB([_res(one=None), _res(one=None)])
    _patch_db(monkeypatch, db)
    r = await W._reconcile_instagram_publish_async("p", "a")
    assert r["recovered"] is False

    # target already published
    post = _post()
    acct = _acct(platform="instagram")
    target = _target(status="published")
    db2 = _FakeDB([_res(one=post), _res(one=acct), _res(one=target)])
    _patch_db(monkeypatch, db2)
    r2 = await W._reconcile_instagram_publish_async("p", "a")
    assert r2["reason"] == "nothing to reconcile"

    # probe fails → retry
    target3 = _target(status="failed")
    db3 = _FakeDB([_res(one=post), _res(one=acct), _res(one=target3)])
    _patch_db(monkeypatch, db3)
    monkeypatch.setattr(W, "_resolve_ig_user_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(W, "decrypt_token", lambda v: "t")
    monkeypatch.setattr(W, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(W, "_instagram_find_live", AsyncMock(side_effect=Exception("probe down")))
    r3 = await W._reconcile_instagram_publish_async("p", "a")
    assert r3 == {"recovered": False, "retry": True}

    # live found → recovered, failed queue row closed
    qrow = _qitem(status=QueueStatus.FAILED)
    target4 = _target(status="failed", error_message="x")
    db4 = _FakeDB(
        [
            _res(one=post),
            _res(one=acct),
            _res(one=target4),
            _res(first=qrow),
        ]
    )
    _patch_db(monkeypatch, db4)
    monkeypatch.setattr(W, "_instagram_find_live", AsyncMock(return_value={"id": "m9", "permalink": "https://ig/m9"}))
    r4 = await W._reconcile_instagram_publish_async("p", "a")
    assert r4 == {"recovered": True, "media_id": "m9"}
    assert target4.status == "published" and target4.platform_post_id == "m9"
    assert qrow.status == QueueStatus.COMPLETED


# ── celery sync wrappers ──────────────────────────────────────────────


def test_sync_wrappers(monkeypatch):
    calls = []

    def _fake_run(coro):
        calls.append(coro)
        coro.close()
        return "ran"

    monkeypatch.setattr(W, "run_async", _fake_run)
    W.process_publish_queue.run()
    assert len(calls) == 2  # queue + d1 sync

    calls.clear()
    W.check_scheduled_posts()
    assert len(calls) == 2

    calls.clear()
    r = W.publish_post_now("p", ["a"])
    assert r == "ran"

    # disabled sweep short-circuits
    monkeypatch.setattr(W, "get_settings", lambda: SimpleNamespace(X_CAPACITY_SWEEP_ENABLED=False))
    r2 = W.requeue_capacity_stuck_x_targets()
    assert r2["disabled"] is True


def test_reconcile_task_retry(monkeypatch):
    def _fake_retry(coro):
        coro.close()
        return {"recovered": False, "retry": True}

    monkeypatch.setattr(W, "run_async", _fake_retry)
    task = W.reconcile_instagram_publish
    with pytest.raises(Exception):
        task.run("p", "a")

    def _fake_ok(coro):
        coro.close()
        return {"recovered": True}

    monkeypatch.setattr(W, "run_async", _fake_ok)
    assert task.run("p", "a") == {"recovered": True}
