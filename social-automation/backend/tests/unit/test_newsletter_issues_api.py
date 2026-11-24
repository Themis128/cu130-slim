"""Coverage for the /newsletter/issues queue-management endpoints.

Endpoints are invoked directly (same style as test_newsletter_linkedin_issue)
with the queue service monkeypatched at the module boundary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

import app.api.newsletter as api
import app.worker.tasks.newsletter_issues as nl_tasks
from app.models.newsletter_issue import NewsletterIssueStatus
from tests.unit.test_newsletter_issue_queue import _FakeDB, _issue


def _request(token: str = "s3cret") -> Request:
    return Request(
        scope={
            "type": "http",
            "headers": [(b"x-service-token", token.encode()), (b"host", b"test")],
        }
    )


def _patched_settings():
    return patch.object(
        api,
        "get_settings",
        return_value=SimpleNamespace(NEWSLETTER_SERVICE_TOKEN="s3cret"),
    )


def _resolved(**kw):
    defaults = dict(
        title="Queued Issue",
        html_body="<p>body</p>",
        text_body=None,
        seo_description="desc",
        post_description=None,
        category=None,
        warnings=[],
        words=10,
        source="explicit",
    )
    defaults.update(kw)
    return api._ResolvedIssue(**defaults)


# ── enqueue ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_issue_future_schedule_no_dispatch(monkeypatch):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    monkeypatch.setattr(api, "_resolve_issue_content", lambda *a, **k: _async_ret(_resolved()))

    async def fake_enqueue(db, **kw):
        assert kw["title"] == "Queued Issue"
        assert kw["render_cover"] is True
        return issue, True

    monkeypatch.setattr(api.newsletter_issue_queue, "enqueue_issue", fake_enqueue)
    with _patched_settings():
        out = await api.enqueue_newsletter_issue(
            api.NewsletterIssueEnqueueRequest(title="Queued Issue", html_body="<p>body</p>", scheduled_for=future),
            _request(),
            db=_FakeDB(),
        )
    assert out["created"] is True and out["source"] == "explicit"
    assert out["status"] == "queued" and out["word_count"] == 10


async def _async_ret(v):
    return v


@pytest.mark.asyncio
async def test_enqueue_issue_due_fires_dispatch_and_dedup(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    monkeypatch.setattr(api, "_resolve_issue_content", lambda *a, **k: _async_ret(_resolved()))
    monkeypatch.setattr(api.newsletter_issue_queue, "enqueue_issue", lambda *a, **k: _async_ret((issue, False)))
    fired = []
    monkeypatch.setattr(nl_tasks, "dispatch_newsletter_issues", SimpleNamespace(delay=lambda *a, **k: fired.append(1)))
    with _patched_settings():
        out = await api.enqueue_newsletter_issue(
            api.NewsletterIssueEnqueueRequest(title="T", html_body="<p>b</p>"),
            _request(),
            db=_FakeDB(),
        )
    # created=False → no dispatch fired even though due
    assert out["created"] is False and fired == []

    # created=True + due → dispatch task fires
    monkeypatch.setattr(
        api.newsletter_issue_queue,
        "enqueue_issue",
        lambda *a, **k: _async_ret((issue, True)),
    )
    with _patched_settings():
        out = await api.enqueue_newsletter_issue(
            api.NewsletterIssueEnqueueRequest(title="T", html_body="<p>b</p>"),
            _request(),
            db=_FakeDB(),
        )
    assert out["created"] is True and fired == [1]

    # broker hiccup on .delay() must not fail the enqueue
    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(nl_tasks, "dispatch_newsletter_issues", SimpleNamespace(delay=boom))
    with _patched_settings():
        out = await api.enqueue_newsletter_issue(
            api.NewsletterIssueEnqueueRequest(title="T", html_body="<p>b</p>"),
            _request(),
            db=_FakeDB(),
        )
    assert out["created"] is True


@pytest.mark.asyncio
async def test_enqueue_issue_bad_schedule_422(monkeypatch):
    monkeypatch.setattr(api, "_resolve_issue_content", lambda *a, **k: _async_ret(_resolved()))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.enqueue_newsletter_issue(
                api.NewsletterIssueEnqueueRequest(title="T", html_body="<p>b</p>", scheduled_for="not-a-date"),
                _request(),
                db=_FakeDB(),
            )
    assert e.value.status_code == 422


# ── list / get ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_and_get_issue(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.PUBLISHED, article_url="https://x/pulse/a/")
    monkeypatch.setattr(api.newsletter_issue_queue, "list_issues", lambda *a, **k: _async_ret([issue]))
    with _patched_settings():
        out = await api.list_newsletter_issues(_request(), status="published", limit=10, db=_FakeDB())
    assert out["issues"][0]["slug"] == issue.slug
    assert out["issues"][0]["article_url"] == "https://x/pulse/a/"

    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(issue))
    with _patched_settings():
        one = await api.get_newsletter_issue(str(issue.id), _request(), db=_FakeDB())
    assert one["id"] == str(issue.id)

    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(None))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.get_newsletter_issue(str(uuid.uuid4()), _request(), db=_FakeDB())
    assert e.value.status_code == 404
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.get_newsletter_issue("not-a-uuid", _request(), db=_FakeDB())
    assert e.value.status_code == 422


# ── cancel / retry ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_and_retry_issue(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(issue))

    async def ok_cancel(db, i):
        return True

    async def ok_retry(db, i):
        return True

    monkeypatch.setattr(api.newsletter_issue_queue, "cancel_issue", ok_cancel)
    monkeypatch.setattr(api.newsletter_issue_queue, "retry_issue", ok_retry)
    with _patched_settings():
        out = await api.cancel_newsletter_issue(str(issue.id), _request(), db=_FakeDB())
        assert out["id"] == str(issue.id)
        out = await api.retry_newsletter_issue(str(issue.id), _request(), db=_FakeDB())
        assert out["id"] == str(issue.id)

    async def no_cancel(db, i):
        return False

    async def no_retry(db, i):
        return False

    monkeypatch.setattr(api.newsletter_issue_queue, "cancel_issue", no_cancel)
    monkeypatch.setattr(api.newsletter_issue_queue, "retry_issue", no_retry)
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.cancel_newsletter_issue(str(issue.id), _request(), db=_FakeDB())
        assert e.value.status_code == 409
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.retry_newsletter_issue(str(issue.id), _request(), db=_FakeDB())
        assert e.value.status_code == 409

    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(None))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.cancel_newsletter_issue(str(uuid.uuid4()), _request(), db=_FakeDB())
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await api.retry_newsletter_issue(str(uuid.uuid4()), _request(), db=_FakeDB())
        assert e.value.status_code == 404


# ── dispatch / verify ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_issue_now(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.QUEUED)
    marked = []
    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(issue))

    async def mark(db, i):
        marked.append(i)

    monkeypatch.setattr(api.newsletter_issue_queue, "mark_due_now", mark)
    fired = []
    monkeypatch.setattr(nl_tasks, "dispatch_newsletter_issues", SimpleNamespace(delay=lambda *a, **k: fired.append(1)))
    with _patched_settings():
        out = await api.dispatch_newsletter_issue_now(str(issue.id), _request(), db=_FakeDB())
    assert out["dispatched"] is True and marked == [issue] and fired == [1]

    def boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(nl_tasks, "dispatch_newsletter_issues", SimpleNamespace(delay=boom))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.dispatch_newsletter_issue_now(str(issue.id), _request(), db=_FakeDB())
    assert e.value.status_code == 503

    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(None))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.dispatch_newsletter_issue_now(str(uuid.uuid4()), _request(), db=_FakeDB())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_verify_issue_now(monkeypatch):
    issue = _issue(status=NewsletterIssueStatus.PUBLISHED, article_url="https://x/pulse/a/")
    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(issue))
    monkeypatch.setattr(api.newsletter_issue_queue, "acquire_dispatch_lock", lambda: _async_ret("tok"))
    released = []

    async def release(tok):
        released.append(tok)

    monkeypatch.setattr(api.newsletter_issue_queue, "release_dispatch_lock", release)

    async def verify(db, i):
        return {"verified": True, "detail": "title found on article page"}

    monkeypatch.setattr(api.newsletter_issue_queue, "verify_issue_article", verify)
    with _patched_settings():
        out = await api.verify_newsletter_issue_now(str(issue.id), _request(), db=_FakeDB())
    assert out["verify"]["verified"] is True and released == ["tok"]

    monkeypatch.setattr(api.newsletter_issue_queue, "acquire_dispatch_lock", lambda: _async_ret(None))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.verify_newsletter_issue_now(str(issue.id), _request(), db=_FakeDB())
    assert e.value.status_code == 409

    monkeypatch.setattr(api.newsletter_issue_queue, "get_issue", lambda *a, **k: _async_ret(None))
    monkeypatch.setattr(api.newsletter_issue_queue, "acquire_dispatch_lock", lambda: _async_ret("tok"))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.verify_newsletter_issue_now(str(uuid.uuid4()), _request(), db=_FakeDB())
    assert e.value.status_code == 404


# ── auth on the new surface ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_issues_endpoints_require_token():
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await api.list_newsletter_issues(_request("wrong"), db=_FakeDB())
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_enqueue_issue_media_spec_stashed_in_result(monkeypatch):
    """media[] on the enqueue body lands in result["_media"] + commits."""
    from app.services import newsletter_publisher

    issue = _issue(status=NewsletterIssueStatus.QUEUED, result={"seed": True})
    db = _FakeDB()
    monkeypatch.setattr(api, "_resolve_issue_content", lambda *a, **k: _async_ret(_resolved()))
    monkeypatch.setattr(api.newsletter_issue_queue, "enqueue_issue", lambda *a, **k: _async_ret((issue, True)))
    spec = [{"type": "image", "asset_id": "abc"}]
    with _patched_settings():
        out = await api.enqueue_newsletter_issue(
            api.NewsletterIssueEnqueueRequest(
                title="Queued Issue",
                html_body="<p>body</p>",
                media=spec,
                scheduled_for="2999-01-01T00:00:00+00:00",
            ),
            _request(),
            db=db,
        )
    assert issue.result["seed"] is True
    assert issue.result[newsletter_publisher.MEDIA_SPEC_KEY] == spec
    assert db.commits == 1
    assert out["created"] is True
