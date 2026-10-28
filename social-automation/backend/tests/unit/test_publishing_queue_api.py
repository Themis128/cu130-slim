"""Tests for app/api/publishing.py — publish-queue CRUD + retry/cancel + history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api import publishing as P
from app.models.content import PostStatus
from app.models.queue import QueueStatus


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


class _DB:
    def __init__(self, results=(), obj=None, scalar=None, fail_commit=False):
        self._q = list(results)
        self._obj = obj
        self._scalar = scalar
        self._fail_commit = fail_commit
        self.added = []
        self.deleted = []
        self.committed = False

    async def get(self, model, pk):
        return self._obj

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    async def scalar(self, stmt):
        return self._scalar

    def add(self, obj):
        self.added.append(obj)
        for field, default in (
            ("id", None),
            ("created_at", None),
            ("attempts", 0),
            ("max_attempts", 3),
        ):
            if getattr(obj, field, None) is None:
                setattr(obj, field, uuid.uuid4() if field == "id" else (datetime.now(UTC) if field == "created_at" else default))

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        if self._fail_commit:
            raise IntegrityError("ins", {}, Exception("dup"))
        self.committed = True

    async def rollback(self):
        self.rolled = True

    async def refresh(self, obj, attrs=None):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)
        if not getattr(obj, "post", None):
            obj.post = SimpleNamespace(content_text="hello world post")
        if not getattr(obj, "social_account", None):
            obj.social_account = SimpleNamespace(platform="twitter")


def _queue(status=QueueStatus.PENDING):
    return SimpleNamespace(
        id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        social_account_id=uuid.uuid4(),
        scheduled_at=datetime.now(UTC),
        priority=0,
        attempts=0,
        max_attempts=3,
        status=status,
        locked_at=None,
        locked_by=None,
        created_at=datetime.now(UTC),
        post=SimpleNamespace(content_text="t"),
        social_account=SimpleNamespace(platform="x"),
    )


def _post(status=PostStatus.DRAFT):
    return SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(), status=status, scheduled_at=None)


@pytest.mark.asyncio
async def test_add_to_queue_post_missing():
    with pytest.raises(HTTPException) as exc:
        await P.add_to_queue(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=_DB())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_add_to_queue_wrong_status():
    post = _post(PostStatus.PUBLISHED)
    with pytest.raises(HTTPException) as exc:
        await P.add_to_queue(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=_DB(results=[post]))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_add_to_queue_account_missing_or_wrong_team():
    post = _post()
    db = _DB(results=[post, None])
    with pytest.raises(HTTPException) as exc:
        await P.add_to_queue(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=db)
    assert exc.value.status_code == 404
    # account from another team
    db2 = _DB(results=[post, SimpleNamespace(id=1, team_id=uuid.uuid4())])
    with pytest.raises(HTTPException) as exc2:
        await P.add_to_queue(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=db2)
    assert exc2.value.status_code == 404


@pytest.mark.asyncio
async def test_add_to_queue_duplicate_active():
    post = _post()
    acc = SimpleNamespace(id=1, team_id=post.team_id)
    db = _DB(results=[post, acc, _queue()])
    with pytest.raises(HTTPException) as exc:
        await P.add_to_queue(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_add_to_queue_success_promotes_draft():
    post = _post(PostStatus.DRAFT)
    acc = SimpleNamespace(id=1, team_id=post.team_id)
    db = _DB(results=[post, acc, None])
    sched = datetime.now(UTC)
    out = await P.add_to_queue(post.id, uuid.uuid4(), sched, priority=5, current_user=SimpleNamespace(id=1), db=db)
    assert post.status == PostStatus.SCHEDULED
    assert post.scheduled_at == sched
    assert db.added[0].priority == 5 and db.added[0].status == QueueStatus.PENDING
    assert out.post_title == "hello world post"


@pytest.mark.asyncio
async def test_add_to_queue_integrity_race_409():
    post = _post(PostStatus.SCHEDULED)
    acc = SimpleNamespace(id=1, team_id=post.team_id)
    db = _DB(results=[post, acc, None], fail_commit=True)
    with pytest.raises(HTTPException) as exc:
        await P.add_to_queue(post.id, uuid.uuid4(), datetime.now(UTC), current_user=SimpleNamespace(id=1), db=db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_list_queue_no_team():
    out = await P.list_queue(uuid.uuid4(), page=1, page_size=20, current_user=SimpleNamespace(id=1), db=_DB())
    assert out.items == [] and out.total == 0


@pytest.mark.asyncio
async def test_list_queue_items():
    q = _queue()
    db = _DB(results=[[q]], obj=SimpleNamespace(id=1), scalar=1)
    out = await P.list_queue(uuid.uuid4(), page=2, page_size=20, status_filter=QueueStatus.FAILED, current_user=SimpleNamespace(id=1), db=db)
    assert out.total == 1 and out.page == 2 and len(out.items) == 1
    assert out.items[0].platform == "x"


@pytest.mark.asyncio
async def test_cancel_scheduled_paths():
    # missing → 404
    with pytest.raises(HTTPException):
        await P.cancel_scheduled(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB())
    # processing → 400
    q = _queue(QueueStatus.PROCESSING)
    with pytest.raises(HTTPException) as exc:
        await P.cancel_scheduled(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB(results=[q]))
    assert exc.value.status_code == 400
    # pending → deleted
    q2 = _queue()
    db = _DB(results=[q2])
    await P.cancel_scheduled(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=db)
    assert q2 in db.deleted and db.committed


@pytest.mark.asyncio
async def test_get_queue_item():
    with pytest.raises(HTTPException):
        await P.get_queue_item(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB())
    q = _queue()
    out = await P.get_queue_item(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB(results=[q]))
    assert out.id == q.id


@pytest.mark.asyncio
async def test_retry_paths():
    for ep in (P.retry_queue_item, P.retry_failed):
        with pytest.raises(HTTPException):
            await ep(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB())
        # pending → 400
        with pytest.raises(HTTPException) as exc:
            await ep(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB(results=[_queue(QueueStatus.PENDING)]))
        assert exc.value.status_code == 400
        # failed → reset to pending
        q = _queue(QueueStatus.FAILED)
        q.attempts = 3
        q.locked_at = datetime.now(UTC)
        q.locked_by = "w1"
        db = _DB(results=[q])
        out = await ep(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=db)
        assert q.status == QueueStatus.PENDING and q.attempts == 0
        assert q.locked_at is None and q.locked_by is None
        assert out.status == QueueStatus.PENDING


@pytest.mark.asyncio
async def test_cancel_queue_item_paths():
    with pytest.raises(HTTPException):
        await P.cancel_queue_item(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB())
    with pytest.raises(HTTPException):
        await P.cancel_queue_item(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB(results=[_queue(QueueStatus.PROCESSING)]))
    q = _queue()
    out = await P.cancel_queue_item(uuid.uuid4(), current_user=SimpleNamespace(id=1), db=_DB(results=[q]))
    assert q.status == QueueStatus.CANCELLED
    assert out.status == QueueStatus.CANCELLED


@pytest.mark.asyncio
async def test_publish_history():
    # no team → []
    assert await P.publish_history(uuid.uuid4(), page=1, page_size=20, current_user=SimpleNamespace(id=1), db=_DB()) == []
    # targets mapped
    t = SimpleNamespace(
        post_id=uuid.uuid4(),
        status="published",
        platform_url="https://x",
        published_at=datetime.now(UTC),
        error_message=None,
        post=SimpleNamespace(content_text="a long body for the title field"),
        social_account=SimpleNamespace(platform="linkedin"),
    )
    t2 = SimpleNamespace(
        post_id=uuid.uuid4(),
        status="failed",
        platform_url=None,
        published_at=None,
        error_message="boom",
        post=SimpleNamespace(content_text=None),
        social_account=SimpleNamespace(platform="tiktok"),
    )
    db = _DB(results=[[t, t2]], obj=SimpleNamespace(id=1))
    out = await P.publish_history(uuid.uuid4(), page=1, page_size=20, current_user=SimpleNamespace(id=1), db=db)
    assert len(out) == 2
    assert out[0]["platform"] == "linkedin" and out[0]["published_at"] is not None
    assert out[1]["post_title"] == "Untitled" and out[1]["error_message"] == "boom"
