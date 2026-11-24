"""Coverage for app/worker/tasks/newsletter_issues.py — the dispatch and
verify Celery tasks. Queue/publisher boundaries are monkeypatched; the
task_session async-CM and run_async bridge are faked."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

import app.services.newsletter_issue_queue as q
import app.services.newsletter_publisher as pub
import app.worker.tasks.newsletter_issues as T
from app.models.newsletter_issue import NewsletterIssueStatus
from tests.unit.test_newsletter_issue_queue import _issue


@asynccontextmanager
async def _fake_session():
    yield object()


def _patch_session(monkeypatch):
    monkeypatch.setattr(T, "task_session", _fake_session)


async def _ret(v):
    return v


# ── dispatch ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_one_success(monkeypatch):
    _patch_session(monkeypatch)
    issue = _issue(title="Pub", status=NewsletterIssueStatus.PUBLISHING)
    updated = _issue(status=NewsletterIssueStatus.PUBLISHED)

    async def fake_publish(i, db=None):
        return {"published": True, "article_url": "https://x/pulse/a/"}, "sidecar", []

    async def fake_record(db, iid, result, via):
        return updated

    monkeypatch.setattr(pub, "publish_stored_issue", fake_publish)
    monkeypatch.setattr(q, "record_publish_result", fake_record)
    out = await T._dispatch_one(issue)
    assert out["ok"] is True and out["via"] == "sidecar"
    assert out["article_url"] == "https://x/pulse/a/" and out["status"] == "published"


@pytest.mark.asyncio
async def test_dispatch_one_failure_and_missing(monkeypatch):
    _patch_session(monkeypatch)
    issue = _issue(title="Fail", status=NewsletterIssueStatus.PUBLISHING)

    async def boom(i, db=None):
        raise RuntimeError("sidecar exploded")

    failed = _issue(status=NewsletterIssueStatus.QUEUED)

    async def fake_fail(db, iid, err):
        return failed

    monkeypatch.setattr(pub, "publish_stored_issue", boom)
    monkeypatch.setattr(q, "record_publish_failure", fake_fail)
    out = await T._dispatch_one(issue)
    assert out["ok"] is False and "sidecar exploded" in out["error"]
    assert out["status"] == "queued"

    async def none_record(db, iid, result, via):
        return None

    async def ok_publish(i, db=None):
        return {"published": True}, "bridge", []

    monkeypatch.setattr(pub, "publish_stored_issue", ok_publish)
    monkeypatch.setattr(q, "record_publish_result", none_record)
    out2 = await T._dispatch_one(issue)
    assert out2["ok"] is True and out2["status"] == "missing"


@pytest.mark.asyncio
async def test_dispatch_lock_held_and_run(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    monkeypatch.setattr(q, "acquire_dispatch_lock", lambda: _ret(None))
    out = await T._dispatch()
    assert out["skipped"] == "lock held" and out["claimed"] == 0

    released = []

    async def acquire():
        return "tok"

    async def release(tok):
        released.append(tok)

    async def claim(db, limit=1):
        assert limit == 1
        return [issue]

    async def one(i):
        return {"id": str(i.id), "ok": True}

    monkeypatch.setattr(q, "acquire_dispatch_lock", acquire)
    monkeypatch.setattr(q, "release_dispatch_lock", release)
    monkeypatch.setattr(q, "claim_due", claim)
    monkeypatch.setattr(T, "_dispatch_one", one)
    _patch_session(monkeypatch)
    out = await T._dispatch()
    assert out["claimed"] == 1 and out["results"][0]["ok"] is True
    assert released == ["tok"]


def test_dispatch_task_wrapper(monkeypatch):
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    out = T.dispatch_newsletter_issues()
    assert "claimed" in out or "skipped" in out or "error" in out

    def boom(coro):
        coro.close()
        raise RuntimeError("loop dead")

    monkeypatch.setattr(T, "run_async", boom)
    out = T.dispatch_newsletter_issues()
    assert out == {"error": "loop dead"}


# ── verify ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_one_found_and_missing(monkeypatch):
    _patch_session(monkeypatch)
    issue = _issue(status=NewsletterIssueStatus.PUBLISHED, article_url="https://x/pulse/a/")
    fresh = _issue(id=issue.id, status=NewsletterIssueStatus.PUBLISHED)

    async def get(db, iid):
        return fresh

    async def verify(db, i):
        return {"verified": True, "detail": "title found"}

    monkeypatch.setattr(q, "get_issue", get)
    monkeypatch.setattr(q, "verify_issue_article", verify)
    out = await T._verify_one(issue)
    assert out["verified"] is True and out["title"] == issue.title

    async def get_none(db, iid):
        return None

    monkeypatch.setattr(q, "get_issue", get_none)
    out = await T._verify_one(issue)
    assert out == {"id": str(issue.id), "error": "missing"}


@pytest.mark.asyncio
async def test_verify_lock_held_and_run(monkeypatch):
    monkeypatch.setattr(q, "acquire_dispatch_lock", lambda: _ret(None))
    out = await T._verify()
    assert out["skipped"] == "lock held" and out["checked"] == 0

    async def acquire():
        return "tok"

    async def release(tok):
        pass

    async def listing(db, limit=3):
        return [_issue(status=NewsletterIssueStatus.PUBLISHED)]

    async def one(i):
        return {"id": str(i.id), "verified": True}

    monkeypatch.setattr(q, "acquire_dispatch_lock", acquire)
    monkeypatch.setattr(q, "release_dispatch_lock", release)
    monkeypatch.setattr(q, "list_issues_needing_verify", listing)
    monkeypatch.setattr(T, "_verify_one", one)
    _patch_session(monkeypatch)
    out = await T._verify()
    assert out["checked"] == 1 and out["results"][0]["verified"] is True


def test_verify_task_wrapper(monkeypatch):
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    out = T.verify_newsletter_issues()
    assert "checked" in out or "skipped" in out or "error" in out

    def boom(coro):
        coro.close()
        raise RuntimeError("loop dead")

    monkeypatch.setattr(T, "run_async", boom)
    out = T.verify_newsletter_issues()
    assert out == {"error": "loop dead"}
