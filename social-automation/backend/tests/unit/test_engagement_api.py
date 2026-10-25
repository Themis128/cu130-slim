"""Human-gate engagement API — pending feed, approve, reject."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import engagement as API


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: list(self.rows),
                first=lambda: self.rows[0] if self.rows else None,
            )
        )

    async def commit(self):
        self.commits += 1


def _row(**kw):
    base = dict(
        id=uuid.uuid4(), platform="linkedin", target_author="ana", action="comment",
        score=9.5, comment_draft="draft", status="pending_human_gate",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_router_routes_registered():
    paths = {(r.path, tuple(sorted(r.methods))) for r in API.router.routes}
    assert ("/engagement/pending", ("GET",)) in paths
    assert ("/engagement/{action_id}/approve", ("POST",)) in paths
    assert ("/engagement/{action_id}/reject", ("POST",)) in paths


@pytest.mark.asyncio
async def test_pending_lists_gate_items():
    r1, r2 = _row(score=9.0), _row(platform="twitter", action="repost", comment_draft=None, score=7.0)
    out = await API.pending(db=_DB([r1, r2]), user=object())
    assert [i.id for i in out] == [str(r1.id), str(r2.id)]
    assert out[1].comment_draft is None
    assert out[1].platform == "twitter"


@pytest.mark.asyncio
async def test_approve_with_edited_draft():
    r = _row()
    db = _DB([r])
    out = await API.approve(str(r.id), API.GateUpdate(comment_draft="edited"), db=db, user=object())
    assert out.status == "pending"
    assert out.comment_draft == "edited"
    assert r.status == "pending"
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, API.GateUpdate()])
async def test_approve_keeps_draft_without_edit(body):
    r = _row()
    out = await API.approve(str(r.id), body, db=_DB([r]), user=object())
    assert out.comment_draft == "draft"
    assert out.status == "pending"


@pytest.mark.asyncio
async def test_approve_and_reject_404():
    aid = str(uuid.uuid4())
    with pytest.raises(HTTPException) as e:
        await API.approve(aid, None, db=_DB([]), user=object())
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await API.reject(aid, db=_DB([]), user=object())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_reject_marks_rejected():
    r = _row()
    db = _DB([r])
    out = await API.reject(str(r.id), db=db, user=object())
    assert out.status == "rejected"
    assert r.status == "rejected"
    assert db.commits == 1
