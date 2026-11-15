"""Unit tests for the platform-owned newsletter issue queue.

Covers slug dedup, the claim/record state machine, retry/backoff,
cancel/retry guards, verify matching, and the Redis dispatch mutex —
all with a fake AsyncSession (no real DB).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.newsletter_issue import NewsletterIssue, NewsletterIssueStatus
from app.services import newsletter_issue_queue as q


class _FakeResult:
    def __init__(self, rows=(), obj=None, rowcount=0):
        self._rows = list(rows)
        self._obj = obj
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._obj

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """Minimal AsyncSession stand-in: scripted execute() results."""

    def __init__(self, results=()):
        self._results = list(results)
        self.commits = 0
        self.rollbacks = 0
        self.added = []

    async def execute(self, stmt):
        if self._results:
            return self._results.pop(0)
        return _FakeResult()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        self.added.append(obj)


def _issue(**kw) -> NewsletterIssue:
    defaults = dict(
        id=uuid.uuid4(),
        slug="test-issue",
        title="Test Issue",
        html_body="<p>body</p>",
        status=NewsletterIssueStatus.QUEUED,
        attempts=0,
        verify_count=0,
        result={},
        last_error="",
        dry_run=False,
        scheduled_for=datetime.now(UTC),
    )
    defaults.update(kw)
    issue = NewsletterIssue(**defaults)
    return issue


# ── slugify ───────────────────────────────────────────────────────────


def test_slugify_explicit_wins():
    assert q.slugify_issue("Hello World!", explicit="My Custom Slug") == "my-custom-slug"


def test_slugify_title_and_issue_number():
    slug = q.slugify_issue("Weekly Cloud Computing Trends — November 02, 2026", issue_number=3)
    assert slug.startswith("weekly-cloud-computing-trends")
    assert slug.endswith("issue-3")
    assert len(slug) <= 220


def test_slugify_unicode_and_empty():
    # Non-ASCII titles collapse to the fallback slug.
    assert q.slugify_issue("Ελληνικά Νέα") == "issue"
    assert q.slugify_issue("!!!") == "issue"


# ── enqueue dedup ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_commits_new_issue():
    db = _FakeDB()
    issue, created = await q.enqueue_issue(db, title="Hello", html_body="<p>x</p>")
    assert created is True
    assert issue.slug == "hello"
    assert db.commits == 1 and db.added == [issue]


@pytest.mark.asyncio
async def test_enqueue_dedup_returns_existing():
    existing = _issue(slug="hello", status=NewsletterIssueStatus.PUBLISHED)
    db = _FakeDB()

    async def _commit_fail():
        db.commits += 1
        raise IntegrityError("dup", None, None)

    db.commit = _commit_fail  # type: ignore[method-assign]
    # After rollback, the re-select returns the existing row.
    db._results = [_FakeResult(obj=existing)]
    issue, created = await q.enqueue_issue(db, title="Hello", html_body="<p>x</p>")
    assert created is False and issue is existing
    assert db.rollbacks == 1


# ── claim / record state machine ──────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_marks_publishing():
    row = _issue(status=NewsletterIssueStatus.QUEUED)
    # execute calls: stale-recovery UPDATE → claim SELECT
    db = _FakeDB(results=[_FakeResult(rowcount=0), _FakeResult(rows=[row])])
    claimed = await q.claim_due(db, limit=1)
    assert claimed == [row]
    assert row.status == NewsletterIssueStatus.PUBLISHING
    assert row.attempts == 1 and row.claimed_at is not None
    assert db.commits == 2  # recovery commit + claim commit


@pytest.mark.asyncio
async def test_record_publish_result_published():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_result(
        db, issue.id, {"published": True, "article_url": "https://x/pulse/a/"}, "sidecar"
    )
    assert out.status == NewsletterIssueStatus.PUBLISHED
    assert out.article_url == "https://x/pulse/a/"
    assert out.published_at is not None and out.last_error == ""
    assert out.result["via"] == "sidecar"


@pytest.mark.asyncio
async def test_record_publish_result_dryrun_terminal():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING, dry_run=True)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_result(db, issue.id, {"published": False, "steps": []}, "sidecar")
    assert out.status == NewsletterIssueStatus.DRYRUN


@pytest.mark.asyncio
async def test_record_publish_result_scheduled_counts_as_published():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_result(db, issue.id, {"scheduled": True}, "bridge")
    assert out.status == NewsletterIssueStatus.PUBLISHED
    assert out.result["via"] == "bridge"


@pytest.mark.asyncio
async def test_record_failure_retries_then_fails():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING, attempts=1)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_failure(db, issue.id, "boom")
    assert out.status == NewsletterIssueStatus.QUEUED
    assert out.scheduled_for > datetime.now(UTC)  # backoff pushed it out
    assert "boom" in out.last_error

    issue2 = _issue(status=NewsletterIssueStatus.PUBLISHING, attempts=q.MAX_PUBLISH_ATTEMPTS)
    db2 = _FakeDB(results=[_FakeResult(obj=issue2)])
    out2 = await q.record_publish_failure(db2, issue2.id, "boom")
    assert out2.status == NewsletterIssueStatus.FAILED


@pytest.mark.asyncio
async def test_cancel_and_retry_guards():
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    db = _FakeDB()
    assert await q.cancel_issue(db, issue) is True
    assert issue.status == NewsletterIssueStatus.CANCELLED
    # cancel again → guard rejects
    assert await q.cancel_issue(db, issue) is False
    # retry from cancelled → queued now
    assert await q.retry_issue(db, issue) is True
    assert issue.status == NewsletterIssueStatus.QUEUED
    # retry while queued → rejected
    assert await q.retry_issue(db, issue) is False


# ── verify ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_issue_article_match(monkeypatch):
    issue = _issue(
        status=NewsletterIssueStatus.PUBLISHED,
        title="The Day Email Went Dark",
        article_url="https://www.linkedin.com/pulse/x/",
    )
    db = _FakeDB()

    class _Client:
        async def navigate(self, url):
            assert "pulse/x" in url
            return {"status": "ok"}

        async def page_text(self):
            return {"url": issue.article_url, "text": "nav chrome The Day Email Went Dark body text"}

    monkeypatch.setattr(
        "app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Client()
    )
    monkeypatch.setattr(q.asyncio, "sleep", lambda *_: _noop())
    result = await q.verify_issue_article(db, issue)
    assert result["verified"] is True
    assert issue.verified_at is not None


async def _noop():
    return None


@pytest.mark.asyncio
async def test_verify_issue_article_miss_and_sidecar_error(monkeypatch):
    issue = _issue(
        status=NewsletterIssueStatus.PUBLISHED,
        title="Missing Title",
        article_url="https://www.linkedin.com/pulse/y/",
    )
    db = _FakeDB()

    class _Client:
        async def navigate(self, url):
            return {"status": "ok"}

        async def page_text(self):
            return {"url": url, "text": "unrelated page content"}

    monkeypatch.setattr(
        "app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Client()
    )
    monkeypatch.setattr(q.asyncio, "sleep", lambda *_: _noop())
    result = await q.verify_issue_article(db, issue)
    assert result["verified"] is False
    assert issue.verified_at is None and issue.verify_count == 1

    class _Dead:
        async def navigate(self, url):
            raise RuntimeError("sidecar down")

    monkeypatch.setattr(
        "app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Dead()
    )
    result2 = await q.verify_issue_article(db, issue)
    assert result2["verified"] is False and "sidecar error" in result2["detail"]
    assert issue.verify_count == 2


# ── dispatch mutex ────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.store = {}
        self.deleted = []

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = val
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_dispatch_lock_acquire_and_release(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: fake)
    token = await q.acquire_dispatch_lock()
    assert token is not None
    # second acquire while held → None
    assert await q.acquire_dispatch_lock() is None
    await q.release_dispatch_lock(token)
    assert q._DISPATCH_LOCK_KEY in fake.deleted
    # free again
    assert await q.acquire_dispatch_lock() is not None


@pytest.mark.asyncio
async def test_dispatch_lock_redis_down_proceeds(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr("redis.asyncio.from_url", _boom)
    token = await q.acquire_dispatch_lock()
    assert token is not None  # best-effort sentinel
    await q.release_dispatch_lock(token)  # must not raise
