"""Branch tests for app/worker/tasks/publishing.py.

These close the alternate arms the main suite (test_worker_publishing.py)
leaves open: queue items with no PostTarget row, recurring posts that skip
the duplicate guards, the repeated-error alert dedup, the error-path
re-fetch misses, and the empty-result arms of the sync wrappers and
small helpers. They reuse that suite's fake DB and builders.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.publishing as W
from app.models.content import PostStatus
from app.models.queue import QueueStatus
from tests.unit.test_worker_publishing import (  # noqa: F401 - _patch_edges is an autouse fixture
    _acct,
    _DBCtx,
    _FakeDB,
    _patch_db,
    _patch_edges,
    _post,
    _qitem,
    _real_worker,
    _res,
    _target,
)


def _queue_db(item, post, acct, target, *, recent=True, extra=()):
    rows = [_res(scalars_all=[item]), _res(one=post), _res(one=acct), _res(one=target)]
    if recent:
        rows.append(_res(scalars_all=[]))
    rows.extend(extra)
    return _FakeDB(rows)


# ── queue loop: no PostTarget row ──────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_text_without_target(monkeypatch):
    item = _qitem()
    db = _FakeDB(
        [
            _res(scalars_all=[item]),
            _res(one=_post(id=item.post_id)),
            _res(one=_acct(id=item.social_account_id)),
            _res(one=None),
            _res(scalars_all=["hi there"]),
        ]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "is_duplicate", lambda a, b, threshold=0.9: True)
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_duplicate_without_target(monkeypatch):
    item = _qitem()
    db = _queue_db(item, _post(id=item.post_id), _acct(id=item.social_account_id), None)
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "media_duplicate_reason", AsyncMock(return_value="same images"))
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    pub.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_gate_block_without_target(monkeypatch):
    item = _qitem()
    db = _queue_db(item, _post(id=item.post_id), _acct(id=item.social_account_id), None)
    _patch_db(monkeypatch, db)
    gate = W.PublishGateResult(passed=False, content="x", overall=40, min_score=80, reason="low")
    monkeypatch.setattr(W, "_enforce_publish_quality", AsyncMock(return_value=gate))
    slack = AsyncMock()
    monkeypatch.setattr(W, "post_alert_to_slack", slack)
    pub = AsyncMock()
    monkeypatch.setattr(W, "publish_to_platform", pub)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    pub.assert_not_awaited()
    assert "40/100" in slack.await_args.args[0]


@pytest.mark.asyncio
async def test_success_without_target_and_no_meta(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id)
    db = _queue_db(item, post, _acct(id=item.social_account_id), None)
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(
        W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=True))
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    # no platform_meta → nothing merged beyond the quality-gate record
    assert set(post.platform_specific) <= {"quality_gate"}
    W._notify_publish_success.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_skip_without_target(monkeypatch):
    item = _qitem()
    db = _queue_db(item, _post(id=item.post_id), _acct(id=item.social_account_id), None)
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(
        W,
        "publish_to_platform",
        AsyncMock(return_value=W.PublishResult(success=False, skipped=True)),
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    W._notify_publish_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_failure_without_target_merges_meta(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    post = _post(id=item.post_id, platform_specific={"ig": {"a": 1}})
    db = _queue_db(item, post, _acct(id=item.social_account_id), None)
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(
        W,
        "publish_to_platform",
        AsyncMock(
            return_value=W.PublishResult(
                success=False, error=None, platform_meta={"ig": {"b": 2}, "flag": True}
            )
        ),
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    assert post.platform_specific["ig"] == {"a": 1, "b": 2}
    assert post.platform_specific["flag"] is True
    # error None → generic reason forwarded to the alert
    assert (
        W._notify_publish_failure.await_args.kwargs["reason"] == "unknown publish error"
    )


@pytest.mark.asyncio
async def test_final_failure_same_error_is_not_realerted(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    target = _target(error_message="  token expired ")
    db = _queue_db(item, _post(id=item.post_id), _acct(id=item.social_account_id), target)
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(
        W,
        "publish_to_platform",
        AsyncMock(
            return_value=W.PublishResult(
                success=False, error="token expired", platform_post_id="half-1"
            )
        ),
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    assert target.status == "failed" and target.platform_post_id == "half-1"
    W._notify_publish_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_non_instagram_does_not_reconcile(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    db = _queue_db(
        item, _post(id=item.post_id), _acct(id=item.social_account_id), _target()
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(
        W,
        "publish_to_platform",
        AsyncMock(return_value=W.PublishResult(success=False, error="?", ambiguous=True)),
    )
    sched = AsyncMock()
    monkeypatch.setattr(W.reconcile_instagram_publish, "apply_async", sched)
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    sched.assert_not_called()


@pytest.mark.asyncio
async def test_recurring_post_skips_duplicate_guards(monkeypatch):
    item = _qitem()
    post = _post(id=item.post_id, is_recurring=True)
    # no "recent texts" query for recurring posts
    db = _queue_db(item, post, _acct(id=item.social_account_id), _target(), recent=False)
    _patch_db(monkeypatch, db)
    media = AsyncMock(return_value="would block")
    monkeypatch.setattr(W, "media_duplicate_reason", media)
    monkeypatch.setattr(
        W, "publish_to_platform", AsyncMock(return_value=W.PublishResult(success=True))
    )
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.COMPLETED
    media.assert_not_awaited()


# ── queue loop: unhandled-exception path variants ──────────────────────


@pytest.mark.asyncio
async def test_exception_not_final_requeues_without_alert(monkeypatch):
    item = _qitem(attempts=0, max_attempts=3)
    db = _queue_db(
        item, _post(id=item.post_id), _acct(id=item.social_account_id), _target()
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("x")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.PENDING and item.attempts == 1
    assert item.locked_by is None
    W._rollup_post_status.assert_not_awaited()
    W._notify_publish_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_exception_final_account_gone_on_refetch(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    post = _post(id=item.post_id)
    db = _queue_db(
        item,
        post,
        _acct(id=item.social_account_id),
        _target(),
        extra=[_res(one=post), _res(one=None)],
    )
    _patch_db(monkeypatch, db)
    status_line = AsyncMock(return_value="1 failed")
    monkeypatch.setattr(W, "_target_status_line", status_line)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("x")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    W._rollup_post_status.assert_awaited_once_with(post, db)
    kw = W._notify_publish_failure.await_args.kwargs
    assert kw["account"] is None and kw["targets_summary"] == "1 failed"


@pytest.mark.asyncio
async def test_exception_final_target_gone_on_refetch(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    db = _queue_db(
        item, post, acct, _target(), extra=[_res(one=post), _res(one=acct), _res(one=None)]
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_target_status_line", AsyncMock(return_value="x"))
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("x")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    W._notify_publish_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_exception_final_refetch_raises_and_no_post(monkeypatch):
    item = _qitem(attempts=2, max_attempts=3)
    db = _queue_db(
        item, _post(id=item.post_id), _acct(id=item.social_account_id), _target()
    )  # refetch hits an empty deque → IndexError → swallowed
    _patch_db(monkeypatch, db)
    status_line = AsyncMock()
    monkeypatch.setattr(W, "_target_status_line", status_line)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("x")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED
    W._rollup_post_status.assert_not_awaited()
    status_line.assert_not_awaited()
    kw = W._notify_publish_failure.await_args.kwargs
    assert kw["post"] is None and kw["targets_summary"] is None


@pytest.mark.asyncio
async def test_exception_final_same_unhandled_error_not_realerted(monkeypatch):
    msg = "Unhandled exception while publishing (see worker logs)"
    item = _qitem(attempts=2, max_attempts=3)
    post = _post(id=item.post_id)
    acct = _acct(id=item.social_account_id)
    tgt = _target(error_message=msg)
    db = _queue_db(item, post, acct, tgt, extra=[_res(one=post), _res(one=acct), _res(one=tgt)])
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "publish_to_platform", AsyncMock(side_effect=RuntimeError("x")))
    await W._process_publish_queue_async()
    assert item.status == QueueStatus.FAILED and tgt.status == "failed"
    W._notify_publish_failure.assert_not_awaited()


# ── publish_post_now: existing active queue row ────────────────────────


@pytest.mark.asyncio
async def test_publish_post_now_existing_row_not_duplicated(monkeypatch):
    post = _post()
    aid = uuid.uuid4()
    db = _FakeDB([_res(one=post), _res(one=_acct(id=aid)), _res(one=SimpleNamespace())])
    _patch_db(monkeypatch, db)
    r = await W._publish_post_now_async(str(post.id), [str(aid)])
    assert r["results"] == [{"account_id": str(aid), "queued": True}]
    assert db.added == []
    assert post.status == PostStatus.PUBLISHING


# ── requeue sweep: media all present ───────────────────────────────────


@pytest.mark.asyncio
async def test_requeue_sweep_with_all_media_present(monkeypatch):
    from datetime import UTC, datetime

    acct = _acct(platform="twitter")
    post = _post(status=PostStatus.FAILED, media_ids=[uuid.uuid4()])
    target = _target(status="failed", error_message="daily cap reached")
    db = _FakeDB(
        results=[_res(all_=[(target, post, acct)])],
        scalars=[0, datetime.now(UTC), 1],  # no active row, recent row, 1/1 media found
    )
    _patch_db(monkeypatch, db)
    r = await W._requeue_capacity_stuck_x_async()
    assert r["requeued"] == [str(post.id)]
    assert target.status == "pending"


# ── reconcile: no FAILED queue row to close ────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_recovered_without_failed_queue_row(monkeypatch):
    post = _post()
    acct = _acct(platform="instagram")
    target = _target(status="failed")
    db = _FakeDB([_res(one=post), _res(one=acct), _res(one=target), _res(first=None)])
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(W, "_resolve_ig_user_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(W, "decrypt_token", lambda v: "t")
    monkeypatch.setattr(W, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(
        W, "_instagram_find_live", AsyncMock(return_value={"id": "m1", "permalink": "u"})
    )
    r = await W._reconcile_instagram_publish_async("p", "a")
    assert r == {"recovered": True, "media_id": "m1"}
    assert target.status == "published"
    assert db.commits == 1


# ── sync wrappers: nothing changed → no D1 sync ────────────────────────


def test_sweep_and_cleanup_skip_sync_when_empty(monkeypatch):
    results = iter([{"requeued": []}, {"deleted": 0}])
    calls = []

    def _fake_run(coro):
        calls.append(coro.__qualname__)
        coro.close()
        return next(results)

    monkeypatch.setattr(W, "run_async", _fake_run)
    monkeypatch.setattr(W, "get_settings", lambda: SimpleNamespace())
    assert W.requeue_capacity_stuck_x_targets() == {"requeued": []}
    assert W.cleanup_publish_queue(3) == {"deleted": 0}
    assert calls == ["_requeue_capacity_stuck_x_async", "_cleanup_publish_queue_async"]


# ── notify/summary helpers (real implementations) ──────────────────────


class _Sess:
    def __init__(self, author):
        self.author = author

    async def execute(self, q):
        return _res(one=self.author)


@pytest.mark.asyncio
async def test_notify_success_email_paths(monkeypatch):
    W2 = _real_worker(monkeypatch)
    monkeypatch.delenv("PUBLISH_SUCCESS_WEBHOOK_URL", raising=False)
    send = AsyncMock()
    monkeypatch.setattr("app.services.email_templates.send_post_published_email", send)
    acct = _acct(platform="linkedin")

    # author opted out of publish emails → nothing sent
    author = SimpleNamespace(notification_preferences={"email_new_post": False})
    monkeypatch.setattr(W2, "task_session", lambda: _DBCtx(_Sess(author)))
    await W2._notify_publish_success(_post(user_id=uuid.uuid4()), acct, None)
    send.assert_not_awaited()

    # author row missing → nothing sent
    monkeypatch.setattr(W2, "task_session", lambda: _DBCtx(_Sess(None)))
    await W2._notify_publish_success(_post(user_id=uuid.uuid4()), acct, None)
    send.assert_not_awaited()

    # default prefs (None) → email goes out
    author2 = SimpleNamespace(notification_preferences=None)
    monkeypatch.setattr(W2, "task_session", lambda: _DBCtx(_Sess(author2)))
    post = _post(user_id=uuid.uuid4())
    await W2._notify_publish_success(post, acct, "https://li/1")
    send.assert_awaited_once_with(author2, post, platform="linkedin")


@pytest.mark.asyncio
async def test_publish_summary_without_preview(monkeypatch):
    W2 = _real_worker(monkeypatch)
    sent = []
    monkeypatch.setattr(W2, "post_publishing_to_slack", AsyncMock(side_effect=sent.append))
    await W2._post_publish_summary_to_slack(_post(content_text=""), [("x", "https://x/1")])
    lines = sent[0].split("\n")
    # no "> preview" quote line when the post has no text
    assert lines[:2] == ["*Published*", "• *x*: https://x/1"]
    assert not any(line.startswith(">") for line in lines)
