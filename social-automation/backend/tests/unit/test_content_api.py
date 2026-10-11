"""Unit tests for app/api/content.py — posts/content router.

Covers post CRUD + lifecycle (schedule, publish-now, duplicate,
cross-post), external-target deletion, list/calendar views, media
filesystem endpoints, approval workflow, comments, pillars, briefs,
and link preview.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.content as C
from app.models.content import PostStatus


class _Res:
    def __init__(self, one=None, all=None):
        self._one = one
        self._all = all

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def first(self):
        return self._one

    def all(self):
        return self._all if self._all is not None else ([self._one] if self._one else [])


class _DB:
    def __init__(self, results=None, team=None, scalar_val=None):
        self._q = list(results or [])
        self._team = team if team is not None else SimpleNamespace(id=uuid.uuid4())
        self._scalar = scalar_val
        self.added = []
        self.deleted = []
        self.commits = 0

    async def get(self, model, key):
        return self._team

    async def scalar(self, q):
        return self._scalar

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    _POST_DEFAULTS = {
        "is_recurring": False,
        "recurrence_interval": 0,
        "recurrence_count": 0,
        "recurrence_max": 0,
        "meta_data": {},
        "media_ids": [],
        "platform_specific": {},
        "hashtags": [],
        "mention_accounts": [],
        "targets": [],
        "comments": [],
    }

    def _backfill(self, o):
        if getattr(o, "id", None) is None:
            o.id = uuid.uuid4()
        tname = type(o).__name__
        if tname == "Post":
            for k, v in self._POST_DEFAULTS.items():
                if getattr(o, k, None) is None:
                    setattr(o, k, v)
            if getattr(o, "recurrence_pattern", None) is None:
                o.recurrence_pattern = C.RecurrencePattern.NONE
            for attr in ("created_at", "updated_at"):
                if getattr(o, attr, None) is None:
                    setattr(o, attr, datetime.now(UTC))
        # PostTarget.social_account stays None — new posts return
        # targets=[] in the response anyway.

    def add(self, o):
        self._backfill(o)
        self.added.append(o)

    async def delete(self, o):
        self.deleted.append(o)

    async def flush(self):
        for o in self.added:
            self._backfill(o)

    async def commit(self):
        self.commits += 1

    async def refresh(self, o, attrs=None):
        for attr, val in (("created_at", datetime.now(UTC)), ("updated_at", datetime.now(UTC))):
            if getattr(o, attr, None) is None:
                try:
                    setattr(o, attr, val)
                except Exception:
                    pass


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="u@x.io")


def _target(account="stub", **kw):
    if account == "stub":
        account = SimpleNamespace(id=uuid.uuid4(), platform="instagram", username="u")
    t = SimpleNamespace(id=uuid.uuid4(), social_account_id=uuid.uuid4(), platform_post_id=None, platform_url=None, status="pending", social_account=account)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _post(**kw):
    p = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=PostStatus.DRAFT,
        content_text="hello",
        media_ids=[],
        platform_specific={},
        hashtags=["tag"],
        mention_accounts=[],
        link_url=None,
        link_preview_override=None,
        scheduled_at=None,
        published_at=None,
        failed_at=None,
        failure_reason=None,
        workflow_id=None,
        workflow_run_id=None,
        meta_data={},
        music_asset_id=None,
        pillar_id=None,
        content_brief_id=None,
        is_recurring=False,
        recurrence_pattern=C.RecurrencePattern.NONE,
        recurrence_interval=0,
        recurrence_count=0,
        recurrence_max=0,
        recurrence_parent_id=None,
        next_recurrence_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        targets=[],
        comments=[],
    )
    for k, v in kw.items():
        setattr(p, k, v)
    return p


@pytest.fixture
def _seams(monkeypatch):
    monkeypatch.setattr(C, "check_quota", AsyncMock())
    monkeypatch.setattr(C, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(C, "strip_embedded_metadata", lambda t, _h, _l: t or "")
    monkeypatch.setattr(C, "render_post_text", lambda _p, _pl: "adapted")
    monkeypatch.setattr(C, "log_action", AsyncMock())


# ── create / list / calendar / get / update ──────────────────────────


@pytest.mark.asyncio
async def test_create_post(_seams, monkeypatch):
    import app.services.pillars as P

    monkeypatch.setattr(P, "least_covered_pillar", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))
    db = _DB()
    out = await C.create_post(C.PostCreate(content_text="hi", target_account_ids=[uuid.uuid4()]), uuid.uuid4(), _user(), db)
    assert out.content_text == "hi"
    kinds = {type(o).__name__ for o in db.added}
    assert "Post" in kinds and "PostTarget" in kinds
    assert out.pillar_id is not None  # auto-assigned least-covered

    # explicit pillar → no auto lookup
    pid = uuid.uuid4()
    monkeypatch.setattr(P, "least_covered_pillar", AsyncMock(side_effect=AssertionError("should not run")))
    db = _DB()
    out = await C.create_post(C.PostCreate(content_text="x", pillar_id=pid, target_account_ids=[]), uuid.uuid4(), _user(), db)
    assert out.pillar_id == pid


@pytest.mark.asyncio
async def test_list_posts_and_calendar(_seams):
    acct = SimpleNamespace(platform="instagram", username="u")
    post = _post(targets=[_target(account=acct)])
    db = _DB(results=[_Res(all=[post])], scalar_val=7)
    out = await C.list_posts(uuid.uuid4(), 1, 20, PostStatus.DRAFT, "he", _user(), db)
    assert out.total == 7 and len(out.posts) == 1

    # calendar formatting
    post2 = _post(content_text="x" * 60, status=PostStatus.SCHEDULED, scheduled_at=datetime.now(UTC), targets=[_target(account=acct)])
    db = _DB(results=[_Res(all=[post2])])
    out = await C.get_calendar(uuid.uuid4(), datetime.now(UTC), datetime.now(UTC), _user(), db)
    assert out[0]["title"].endswith("...")
    assert out[0]["platforms"] == ["instagram"]
    assert out[0]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_get_and_update_post(_seams):
    post = _post()
    out = await C.get_post(post.id, uuid.uuid4(), _user(), _DB(results=[_Res(one=post)]))
    assert out.id == post.id

    with pytest.raises(HTTPException):
        await C.get_post(uuid.uuid4(), uuid.uuid4(), _user(), _DB())

    # published → cannot edit
    pub = _post(status=PostStatus.PUBLISHED)
    with pytest.raises(HTTPException) as ei:
        await C.update_post(pub.id, C.PostUpdate(content_text="x", target_account_ids=None), uuid.uuid4(), _user(), _DB(results=[_Res(one=pub)]))
    assert ei.value.status_code == 400

    # update: content corrected, targets swapped, schedule→status sync
    old_t = _target()
    post = _post(status=PostStatus.DRAFT, targets=[old_t])
    aid = uuid.uuid4()
    db = _DB(results=[_Res(one=post)])
    out = await C.update_post(
        post.id, C.PostUpdate(content_text="new text", target_account_ids=[aid], scheduled_at=datetime.now(UTC)), uuid.uuid4(), _user(), db
    )
    assert post.content_text == "new text"
    assert post.status == PostStatus.SCHEDULED  # scheduled_at set
    assert old_t in db.deleted
    assert any(type(o).__name__ == "PostTarget" for o in db.added)


@pytest.mark.asyncio
async def test_delete_post_and_external(_seams, monkeypatch):
    # draft → straight delete
    post = _post()
    db = _DB(results=[_Res(one=post)])
    await C.delete_post(post.id, uuid.uuid4(), _user(), db)
    assert db.deleted == [post]

    # published w/o flag → 400
    pub = _post(status=PostStatus.PUBLISHED)
    with pytest.raises(HTTPException) as ei:
        await C.delete_post(pub.id, uuid.uuid4(), _user(), _DB(results=[_Res(one=pub)]), delete_external=False)
    assert ei.value.status_code == 400

    # external delete: threads ok + unsupported platform reported
    acct_t = SimpleNamespace(platform="threads", account_id="u1", access_token_enc=b"enc")
    acct_x = SimpleNamespace(platform="tiktok", account_id="u2", access_token_enc=b"enc")
    pub.targets = [
        _target(account=acct_t, platform_post_id="pp1", status="published"),
        _target(account=acct_x, platform_post_id="pp2", status="published"),
    ]
    import app.core.security as SEC
    import app.services.threads_api as TA

    monkeypatch.setattr(SEC, "decrypt_token", lambda b: "tok")
    client = SimpleNamespace(delete_post=AsyncMock(return_value=True))
    monkeypatch.setattr(TA, "ThreadsAPIClient", lambda **kw: client)
    errors = await C._delete_external_targets(pub, _DB(results=[_Res(one=pub)]))
    assert "tiktok" in errors and "threads" not in errors
    assert pub.targets[0].status == "deleted"

    # external error → 502 with per-platform detail
    pub2 = _post(status=PostStatus.PUBLISHED, targets=[_target(account=acct_x, platform_post_id="pp", status="published")])
    with pytest.raises(HTTPException) as ei:
        await C.delete_post(pub2.id, uuid.uuid4(), _user(), _DB(results=[_Res(one=pub2)]), delete_external=True)
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_schedule_and_publish_now(_seams, monkeypatch):
    # bad datetime → 422
    with pytest.raises(HTTPException) as ei:
        await C.schedule_post(uuid.uuid4(), "not-a-date", uuid.uuid4(), _user(), _DB())
    assert ei.value.status_code == 422

    post = _post()
    out = await C.schedule_post(post.id, "2030-01-01T10:00:00Z", uuid.uuid4(), _user(), _DB(results=[_Res(one=post)]))
    assert post.status == PostStatus.SCHEDULED
    assert out.scheduled_at is not None

    # publish-now: no targets → 400 (after brand+voice executes)
    post2 = _post(targets=[])
    db = _DB(results=[_Res(one=post2), _Res(), _Res()])
    with pytest.raises(HTTPException) as ei:
        await C.publish_now(post2.id, uuid.uuid4(), _user(), db)
    assert ei.value.status_code == 400

    # happy path: schedules celery tasks via executor
    import app.worker.tasks.publishing as WP

    monkeypatch.setattr(WP, "publish_post_now", SimpleNamespace(delay=Mock()))
    monkeypatch.setattr(WP, "process_publish_queue", SimpleNamespace(apply_async=Mock()))
    post3 = _post(targets=[_target()])
    db = _DB(results=[_Res(one=post3), _Res(), _Res()])
    out = await C.publish_now(post3.id, uuid.uuid4(), _user(), db)
    assert out.status == PostStatus.SCHEDULED
    WP.publish_post_now.delay.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_and_cross_post(_seams, monkeypatch):
    post = _post(targets=[_target()])
    db = _DB(results=[_Res(one=post)])
    out = await C.duplicate_post(post.id, uuid.uuid4(), _user(), db)
    assert out.status == PostStatus.DRAFT
    assert len([o for o in db.added if type(o).__name__ == "PostTarget"]) == 1

    # cross-post: account auto-select (fb prefers page/business)
    biz = SimpleNamespace(id=uuid.uuid4(), platform="facebook", account_type="page", is_business=True)
    personal = SimpleNamespace(id=uuid.uuid4(), platform="facebook", account_type="profile", is_business=False)
    post2 = _post()
    db = _DB(results=[_Res(one=post2), _Res(all=[personal, biz])])
    req = C.CrossPostRequest(target_platform="facebook")
    await C.cross_post_to_platform(post2.id, req, uuid.uuid4(), _user(), db)
    tgt = [o for o in db.added if type(o).__name__ == "PostTarget"][0]
    assert tgt.social_account_id == biz.id  # page preferred

    # no candidate → 404
    db = _DB(results=[_Res(one=post2), _Res(all=[])])
    with pytest.raises(HTTPException) as ei:
        await C.cross_post_to_platform(post2.id, req, uuid.uuid4(), _user(), db)
    assert ei.value.status_code == 404


# ── approval workflow ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_workflow(_seams):
    # wrong status guards
    post = _post(status=PostStatus.DRAFT)
    with pytest.raises(HTTPException):
        await C.approve_post(post.id, uuid.uuid4(), None, _user(), _DB(results=[_Res(one=post)]))
    with pytest.raises(HTTPException):
        await C.reject_post(post.id, uuid.uuid4(), None, _user(), _DB(results=[_Res(one=post)]))

    # submit → REVIEW → approve → APPROVED → comment records action
    db = _DB(results=[_Res(one=post)])
    out = await C.submit_for_review(post.id, uuid.uuid4(), _user(), db)
    assert post.status == PostStatus.REVIEW
    assert any(type(o).__name__ == "PostComment" for o in db.added)

    db = _DB(results=[_Res(one=post)])
    await C.approve_post(post.id, uuid.uuid4(), C.CommentCreate(body="LGTM", action="approve"), _user(), db)
    assert post.status == PostStatus.APPROVED

    # reject path on a fresh REVIEW post
    post2 = _post(status=PostStatus.REVIEW)
    await C.reject_post(post2.id, uuid.uuid4(), None, _user(), _DB(results=[_Res(one=post2)]))
    assert post2.status == PostStatus.DRAFT

    # add_comment
    post3 = _post()
    db = _DB(results=[_Res(one=post3)])
    out = await C.add_comment(post3.id, C.CommentCreate(body="note"), uuid.uuid4(), _user(), db)
    assert out.body == "note" and out.action == "comment"


# ── pillars / briefs / link preview ──────────────────────────────────


@pytest.mark.asyncio
async def test_pillars_and_briefs(_seams):
    pil = SimpleNamespace(id=uuid.uuid4())
    out = await C.list_pillars(uuid.uuid4(), _user(), _DB(results=[_Res(all=[pil])]))
    assert out == [pil]

    db = _DB()
    await C.create_pillar(C.PillarCreate(name="P", keywords=["k"]), uuid.uuid4(), _user(), db)
    assert len(db.added) == 1

    db = _DB(results=[_Res(one=pil)])
    await C.update_pillar(pil.id, C.PillarCreate(name="P2"), uuid.uuid4(), _user(), db)
    assert pil.name == "P2"

    db = _DB(results=[_Res(one=pil)])
    await C.delete_pillar(pil.id, uuid.uuid4(), _user(), db)
    assert db.deleted == [pil]
    # delete is idempotent — no 404 on missing
    await C.delete_pillar(uuid.uuid4(), uuid.uuid4(), _user(), _DB())
    with pytest.raises(HTTPException) as ei:
        await C.update_pillar(uuid.uuid4(), C.PillarCreate(name="x"), uuid.uuid4(), _user(), _DB())
    assert ei.value.status_code == 404

    # briefs
    db = _DB()
    await C.create_brief(C.BriefCreate(title="B"), uuid.uuid4(), _user(), db)
    assert len(db.added) == 1
    db = _DB(results=[_Res(all=["b1"])])
    assert await C.list_briefs(uuid.uuid4(), _user(), db) == ["b1"]
    brief = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[_Res(one=brief)])
    await C.delete_brief(brief.id, uuid.uuid4(), _user(), db)
    assert db.deleted == [brief]


@pytest.mark.asyncio
async def test_link_preview(monkeypatch):
    import app.services.link_preview as LP

    monkeypatch.setattr(LP, "fetch_link_preview", AsyncMock(return_value={"url": "u", "title": "t"}))
    out = await C.link_preview("https://x.io", _user())
    assert out["title"] == "t"
    monkeypatch.setattr(LP, "fetch_link_preview", AsyncMock(side_effect=ValueError("bad url")))
    with pytest.raises(HTTPException) as ei:
        await C.link_preview("bad", _user())
    assert ei.value.status_code == 422
    monkeypatch.setattr(LP, "fetch_link_preview", AsyncMock(side_effect=RuntimeError("net")))
    with pytest.raises(HTTPException) as ei:
        await C.link_preview("https://x.io", _user())
    assert ei.value.status_code == 502


# ── media filesystem endpoints ───────────────────────────────────────


@pytest.mark.asyncio
async def test_media_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "MEDIA_DIR", tmp_path)
    (tmp_path / "a.png").write_bytes(b"x" * 10)
    (tmp_path / "b.mp4").write_bytes(b"y")
    out = await C.list_content_media(1, 20, None, _user())
    assert out["total"] == 2
    out = await C.list_content_media(1, 20, "image", _user())
    assert out["total"] == 1 and out["items"][0]["media_type"] == "image"

    # upload
    import io

    f = SimpleNamespace(filename="pic.png", file=io.BytesIO(b"data"))
    out = await C.upload_content_media(f, _user())
    assert out["filename"].endswith("_pic.png")
    assert out["size"] == 4

    # delete: bad id → 400; existing file removed
    with pytest.raises(HTTPException):
        await C.delete_content_media("bad id!", _user())
    # delete: uploaded uuid-prefixed id is accepted
    uploaded = out["filename"]
    assert (tmp_path / uploaded).exists()
    await C.delete_content_media(uploaded, _user())
    assert not (tmp_path / uploaded).exists()
    target = tmp_path / "deadbeef_x.png"
    target.write_bytes(b"z")
    await C.delete_content_media("deadbeef_x.png", _user())
    assert not target.exists()
