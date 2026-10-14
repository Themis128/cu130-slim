"""Tests for POST /newsletter/linkedin/issue — the LinkedIn newsletter
publishing endpoint. Real code under test; stubs only at true external
boundaries (sidecar httpx, R2 article fetch, DMR generation). The cover
renderer and HTML converters run for real.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request
from PIL import Image

import app.api.newsletter as newsletter_api
import app.services.linkedin_sidecar as sidecar_mod


def _request(token: str = "s3cret") -> Request:
    return Request(
        scope={
            "type": "http",
            "headers": [(b"x-service-token", token.encode()), (b"host", b"test")],
        }
    )


def _patched_settings():
    return patch.object(
        newsletter_api,
        "get_settings",
        return_value=SimpleNamespace(NEWSLETTER_SERVICE_TOKEN="s3cret"),
    )


class _Http:
    """Records sidecar calls; returns a canned publish result."""

    def __init__(self, status=200, body=None, text=""):
        self.status = status
        self.body = body or {"status": "ok", "published": True, "steps": ["editor-ready"]}
        self.text = text
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def post(self, url, **kw):
        self.calls.append((url, kw))
        return SimpleNamespace(
            status_code=self.status, text=self.text, json=lambda: self.body
        )


def _stub_sidecar(monkeypatch, http=None):
    http = http or _Http()
    monkeypatch.setattr(sidecar_mod.httpx, "AsyncClient", lambda *a, **k: http)
    return http


def _long_body(words: int = 900) -> str:
    return "<p>" + " ".join(f"w{i}" for i in range(words)) + "</p>"


# ── auth ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_401_without_token(monkeypatch):
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(title="t", text_body="b"),
                _request("wrong"),
                db=None,
            )
    assert e.value.status_code == 401


# ── explicit content mode ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_explicit_forwards_to_sidecar(monkeypatch):
    http = _stub_sidecar(monkeypatch)
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(
                title="Issue #2 — real numbers",
                html_body=_long_body(1300),
                seo_description="desc",
                post_description="feed text",
                dry_run=False,
            ),
            _request(),
            db=None,
        )
    assert resp.status == "ok" and resp.published is True
    assert resp.source == "explicit" and resp.word_count == 1300
    url, kw = http.calls[-1]
    assert url == "/newsletter/publish"
    p = kw["json"]
    assert p["title"] == "Issue #2 — real numbers"
    assert p["seo_title"] == "Issue #2 — real numbers"  # defaults to title
    assert p["seo_description"] == "desc"
    assert p["post_description"] == "feed text"
    assert p["newsletter_name"] == "The Cloudless Build Log"
    # Real branded cover rendered and forwarded as base64 PNG
    assert p["cover_base64"]
    import base64

    img = Image.open(io.BytesIO(base64.b64decode(p["cover_base64"])))
    assert img.format == "PNG"


@pytest.mark.asyncio
async def test_issue_short_body_warns(monkeypatch):
    _stub_sidecar(monkeypatch)
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(title="t", text_body="tiny body"),
            _request(),
            db=None,
        )
    assert any("1200+" in w for w in resp.warnings)


@pytest.mark.asyncio
async def test_issue_dry_run_propagates(monkeypatch):
    http = _stub_sidecar(monkeypatch)
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(
                title="t", text_body="b", dry_run=True, render_cover=False
            ),
            _request(),
            db=None,
        )
    assert resp.dry_run is True
    assert http.calls[-1][1]["json"]["dry_run"] is True
    assert "cover_base64" not in http.calls[-1][1]["json"]


# ── article_slug mode ────────────────────────────────────────────────

ARTICLE = {
    "title": "The free-tier CDN playbook",
    "contentMarkdown": "Intro para.\n\n## Section one\n\nBody text.\n\n- point a\n- point b",
    "excerpt": "A real excerpt under 160 chars.",
    "socialPost": "New issue is out — read more https://x #tag",
    "category": "Cloud",
}


@pytest.mark.asyncio
async def test_issue_article_slug_mode(monkeypatch):
    http = _stub_sidecar(monkeypatch)

    async def fake_get(slug):
        assert slug == "2026-10-12-cdn"
        return ARTICLE

    monkeypatch.setattr(newsletter_api.blog_articles, "get_published_article", fake_get)
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(article_slug="2026-10-12-cdn"),
            _request(),
            db=None,
        )
    assert resp.source == "article_slug"
    assert resp.title == "The free-tier CDN playbook"
    p = http.calls[-1][1]["json"]
    assert "<h2>Section one</h2>" in p["html_body"]
    assert "<li>point a</li>" in p["html_body"]
    assert "<li>point b</li>" in p["html_body"]
    assert p["seo_description"] == "A real excerpt under 160 chars."
    # socialPost links stripped by the real clean_social_post
    assert "https://" not in (p["post_description"] or "")


@pytest.mark.asyncio
async def test_issue_article_slug_404(monkeypatch):
    async def fake_get(slug):
        return None

    monkeypatch.setattr(newsletter_api.blog_articles, "get_published_article", fake_get)
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(article_slug="nope"),
                _request(),
                db=None,
            )
    assert e.value.status_code == 404


# ── topic (generate) mode ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_topic_mode_generates_and_assembles(monkeypatch):
    http = _stub_sidecar(monkeypatch)

    async def fake_generate(topic, **kw):
        assert topic == "self-hosted CI costs"
        assert kw["sections"] == 9
        return {
            "title": "What a self-hosted CI actually costs",
            "subtitle": "Real babysitting numbers.",
            "sections": [
                {"heading": "The bill", "body": "Ten hours a week."},
                {"heading": "What breaks", "body": "Runners die."},
            ],
            "takeaways": ["Budget the babysitting", "Pin every dependency"],
            "cta": "What does your CI cost you?",
        }

    monkeypatch.setattr(newsletter_api, "generate_linkedin_article", fake_generate)
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(topic="self-hosted CI costs"),
            _request(),
            db=None,
        )
    assert resp.source == "generated"
    assert resp.title == "What a self-hosted CI actually costs"
    p = http.calls[-1][1]["json"]
    assert "<h2>The bill</h2>" in p["html_body"]
    assert "<h2>Key takeaways</h2>" in p["html_body"]
    assert "<li>Budget the babysitting</li>" in p["html_body"]
    assert "What does your CI cost you?" in p["html_body"]
    assert p["seo_description"] == "Real babysitting numbers."


# ── validation + error mapping ───────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_422_no_content_source(monkeypatch):
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(), _request(), db=None
            )
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_issue_422_min_words(monkeypatch):
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(
                    title="t", text_body="short", min_words=100
                ),
                _request(),
                db=None,
            )
    assert e.value.status_code == 422 and "min_words" in e.value.detail


@pytest.mark.asyncio
async def test_issue_502_on_sidecar_error(monkeypatch):
    _stub_sidecar(monkeypatch, _Http(status=500, text="editor dead"))
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(title="t", text_body="b"),
                _request(),
                db=None,
            )
    assert e.value.status_code == 502 and "editor dead" in e.value.detail
