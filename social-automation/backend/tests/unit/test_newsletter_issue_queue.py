"""Unit tests for the platform-owned newsletter issue queue.

Covers slug dedup, the claim/record state machine, retry/backoff,
cancel/retry guards, verify matching, and the Redis dispatch mutex —
all with a fake AsyncSession (no real DB).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
    out = await q.record_publish_result(db, issue.id, {"published": True, "article_url": "https://x/pulse/a/"}, "sidecar")
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
async def test_circuit_open_error_waits_for_circuit_expiry():
    until = datetime.now(UTC) + timedelta(hours=2)
    err = (
        'sidecar: {"error":"LinkedIn circuit open until '
        + until.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + 'Z (redirect_loop); skipped navigate https://www.linkedin.com/feed/"}; bridge: {"detail":"busy"}'
    )
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING, attempts=1)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_failure(db, issue.id, err)
    assert out.status == NewsletterIssueStatus.QUEUED
    # Scheduled just past the circuit's `until`, not the linear backoff.
    assert out.scheduled_for > datetime.now(UTC) + timedelta(hours=1)
    assert out.scheduled_for < until + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_circuit_open_error_survives_max_attempts():
    """A circuit-blocked failure is infra-side — it doesn't die at MAX attempts."""
    until = datetime.now(UTC) + timedelta(hours=1)
    err = f'LinkedIn circuit open until {until.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z (redirect_loop); skipped navigate x'
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING, attempts=q.MAX_PUBLISH_ATTEMPTS)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_failure(db, issue.id, err)
    assert out.status == NewsletterIssueStatus.QUEUED
    assert out.scheduled_for > datetime.now(UTC) + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_circuit_wait_capped():
    until = datetime.now(UTC) + timedelta(hours=1)
    err = f'LinkedIn circuit open until {until.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]}Z (redirect_loop); skipped navigate x'
    issue = _issue(status=NewsletterIssueStatus.PUBLISHING, attempts=q.MAX_CIRCUIT_WAIT_ATTEMPTS)
    db = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_failure(db, issue.id, err)
    assert out.status == NewsletterIssueStatus.FAILED


@pytest.mark.asyncio
async def test_circuit_parse_variants():
    assert q._circuit_open_until("boom") is None
    assert q._circuit_open_until('circuit open until (redirect_loop)') is None
    future = datetime.now(UTC) + timedelta(hours=3)
    out = q._circuit_open_until(
        'LinkedIn circuit open until '
        + future.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + 'Z (redirect_loop); skipped navigate x'
    )
    assert out == future.replace(microsecond=(future.microsecond // 1000) * 1000)
    # An `until` already in the past clamps to now (retry almost immediately).
    past = q._circuit_open_until('circuit open until 2020-01-01T00:00:00.000Z (redirect_loop)')
    assert past >= datetime.now(UTC) - timedelta(seconds=5)


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

    monkeypatch.setattr("app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Client())
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

    monkeypatch.setattr("app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Client())
    monkeypatch.setattr(q.asyncio, "sleep", lambda *_: _noop())
    result = await q.verify_issue_article(db, issue)
    assert result["verified"] is False
    assert issue.verified_at is None and issue.verify_count == 1

    class _Dead:
        async def navigate(self, url):
            raise RuntimeError("sidecar down")

    monkeypatch.setattr("app.services.linkedin_sidecar.LinkedInSidecarClient", lambda *a, **k: _Dead())
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


# ── remaining queue branches ──────────────────────────────────────────


def test_slugify_explicit_blank_falls_through():
    # An explicit slug that sanitizes to nothing falls back to the title.
    assert q.slugify_issue("Hello World", explicit="!!!") == "hello-world"


@pytest.mark.asyncio
async def test_enqueue_integrity_error_reraises_when_no_existing():
    db = _FakeDB()

    async def _commit_fail():
        db.commits += 1
        raise IntegrityError("dup", None, None)

    db.commit = _commit_fail  # type: ignore[method-assign]
    db._results = [_FakeResult(obj=None)]
    with pytest.raises(IntegrityError):
        await q.enqueue_issue(db, title="Hello", html_body="<p>x</p>")


@pytest.mark.asyncio
async def test_get_issue_and_list_issues():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHED)
    db = _FakeDB(results=[_FakeResult(obj=issue), _FakeResult(rows=[issue])])
    assert await q.get_issue(db, issue.id) is issue
    out = await q.list_issues(db, status="published", limit=10)
    assert out == [issue]


@pytest.mark.asyncio
async def test_record_publish_result_missing_and_unpublished():
    db = _FakeDB(results=[_FakeResult(obj=None)])
    assert await q.record_publish_result(db, uuid.uuid4(), {}, "sidecar") is None

    issue = _issue(status=NewsletterIssueStatus.PUBLISHING)
    db2 = _FakeDB(results=[_FakeResult(obj=issue)])
    out = await q.record_publish_result(db2, issue.id, {}, "sidecar")
    assert out.status == NewsletterIssueStatus.FAILED
    assert "without published" in out.last_error


@pytest.mark.asyncio
async def test_record_failure_missing_issue():
    db = _FakeDB(results=[_FakeResult(obj=None)])
    assert await q.record_publish_failure(db, uuid.uuid4(), "x") is None


@pytest.mark.asyncio
async def test_mark_due_now_only_when_queued():
    db = _FakeDB()
    published = _issue(status=NewsletterIssueStatus.PUBLISHED)
    await q.mark_due_now(db, published)
    assert db.commits == 0
    queued = _issue(status=NewsletterIssueStatus.QUEUED)
    await q.mark_due_now(db, queued)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_verify_no_article_url():
    issue = _issue(status=NewsletterIssueStatus.PUBLISHED, article_url=None)
    out = await q.verify_issue_article(_FakeDB(), issue)
    assert out["verified"] is False and "no article_url" in out["detail"]


@pytest.mark.asyncio
async def test_list_issues_needing_verify():
    rows = [_issue(status=NewsletterIssueStatus.PUBLISHED)]
    db = _FakeDB(results=[_FakeResult(rows=rows)])
    out = await q.list_issues_needing_verify(db, limit=5)
    assert out == rows


@pytest.mark.asyncio
async def test_release_lock_no_token_and_error(monkeypatch):
    await q.release_dispatch_lock(None)  # early return, no redis

    def _boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr("redis.asyncio.from_url", _boom)
    await q.release_dispatch_lock("tok")  # exception swallowed


# ── publisher: publish_stored_issue ───────────────────────────────────


@pytest.mark.asyncio
async def test_publish_stored_issue_rendered_and_explicit_cover(monkeypatch):
    import app.services.newsletter_cover as cover
    import app.services.newsletter_publisher as pub

    seen = {}

    async def fake_fallback(**kw):
        seen.update(kw)
        return {"published": True}, "sidecar", []

    monkeypatch.setattr(pub, "publish_with_fallback", fake_fallback)
    monkeypatch.setattr(cover, "render_newsletter_cover", lambda *a, **k: b"PNG")

    issue = _issue(title="Stored", html_body="<p>x</p>")
    issue.cover_base64 = None
    issue.render_cover = True
    issue.newsletter_name = "The Cloudless Build Log"
    issue.seo_title = None
    issue.seo_description = "desc"
    issue.post_description = "post"
    issue.dry_run = False

    result, via, warnings = await pub.publish_stored_issue(issue)
    assert via == "sidecar" and result["published"] is True and warnings == []
    assert seen["title"] == "Stored" and seen["seo_title"] == "Stored"
    assert seen["cover_base64"]  # rendered cover was passed through

    issue2 = _issue(title="NoRender")
    issue2.cover_base64 = "QUJD"
    issue2.render_cover = False
    await pub.publish_stored_issue(issue2)
    assert seen["cover_base64"] == "QUJD"


# ── publisher: resolve_media_items ────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_media_items_none_and_passthrough(monkeypatch):
    import app.services.newsletter_publisher as pub
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "https://media.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "")

    assert await pub.resolve_media_items(None, None) == []
    assert await pub.resolve_media_items(None, []) == []

    out = await pub.resolve_media_items(None, [
        42,  # non-dict entries are dropped, never fatal
        {"type": "video", "url": "https://media.test/v.mp4", "filename": "v.mp4"},
        {"type": "image", "base64": "QUJD", "filename": "a.png"},
        {"base64": "QUJD"},  # no type -> image
        {"type": "image", "url": "https://evil.example/x.png"},  # SSRF guard drop
        {"type": "image", "url": "ftp://media.test/x.png"},  # bad scheme
        {"type": "image", "url": "notaurl"},  # no hostname
        {"type": "image", "url": "https://media.test/x.png", "base64": "QUJD"},  # base64 wins, url unchecked
    ])
    assert out == [
        {"type": "video", "base64": None, "url": "https://media.test/v.mp4", "filename": "v.mp4"},
        {"type": "image", "base64": "QUJD", "url": None, "filename": "a.png"},
        {"type": "image", "base64": "QUJD", "url": None, "filename": None},
        {"type": "image", "base64": "QUJD", "url": "https://media.test/x.png", "filename": None},
    ]


def test_media_url_allowed_branches(monkeypatch):
    import app.services.newsletter_publisher as pub
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "media.test")  # no scheme -> https assumed
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://r2.test/")

    assert pub._media_url_allowed("https://media.test/x.png") is True
    assert pub._media_url_allowed("https://r2.test/x.png") is True
    assert pub._media_url_allowed("https://other.test/x.png") is False

    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "")
    assert pub._media_url_allowed("https://media.test/x.png") is False

    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://[bad")  # unparseable base skipped
    assert pub._media_url_allowed("https://r2.test/x.png") is False


@pytest.mark.asyncio
async def test_resolve_media_items_asset_paths(monkeypatch):
    from types import SimpleNamespace

    import app.services.media_dedup as dedup
    import app.services.newsletter_publisher as pub

    asset = SimpleNamespace(filename="a.png")
    db = _FakeDB(results=[_FakeResult(obj=asset)])
    async def fake_load(a):
        return b"PNG"

    monkeypatch.setattr(dedup, "load_asset_bytes", fake_load)

    out = await pub.resolve_media_items(db, [{"type": "image", "asset_id": "x"}])
    assert out == [{"type": "image", "base64": "UE5H", "filename": "a.png"}]

    db3 = _FakeDB(results=[_FakeResult(obj=asset)])
    out = await pub.resolve_media_items(db3, [{"asset_id": "x", "filename": "custom.png"}])
    assert out[0]["filename"] == "custom.png"

    # asset lookup miss -> dropped
    assert await pub.resolve_media_items(
        _FakeDB(results=[_FakeResult(obj=None)]), [{"asset_id": "x"}]
    ) == []
    # no db session -> asset_id items cannot resolve
    assert await pub.resolve_media_items(None, [{"asset_id": "x"}]) == []

    async def boom(a):
        raise RuntimeError("io")

    monkeypatch.setattr(dedup, "load_asset_bytes", boom)
    assert await pub.resolve_media_items(
        _FakeDB(results=[_FakeResult(obj=asset)]), [{"asset_id": "x"}]
    ) == []


@pytest.mark.asyncio
async def test_publish_stored_issue_forwards_media(monkeypatch):
    import app.services.newsletter_cover as cover
    import app.services.newsletter_publisher as pub

    seen = {}

    async def fake_fallback(**kw):
        seen.update(kw)
        return {"published": True}, "sidecar", []

    monkeypatch.setattr(pub, "publish_with_fallback", fake_fallback)
    monkeypatch.setattr(cover, "render_newsletter_cover", lambda *a, **k: b"PNG")

    issue = _issue(title="Media")
    issue.cover_base64 = "QUJD"
    issue.render_cover = False
    issue.result = {pub.MEDIA_SPEC_KEY: [{"type": "image", "base64": "QUJD"}]}
    await pub.publish_stored_issue(issue, None)
    assert seen["media"] == [
        {"type": "image", "base64": "QUJD", "url": None, "filename": None}
    ]

    issue2 = _issue(title="NoMedia")
    issue2.cover_base64 = "QUJD"
    issue2.render_cover = False
    issue2.result = {}
    await pub.publish_stored_issue(issue2, None)
    assert seen["media"] is None
