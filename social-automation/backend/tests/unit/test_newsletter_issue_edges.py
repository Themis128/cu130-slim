"""Edge branches of POST /newsletter/linkedin/issue: generator overrides,
untitled generation, sidecar transport errors and sidecar-only mode."""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

import app.api.newsletter as newsletter_api
import app.services.linkedin_sidecar as sidecar_mod
from tests.unit.test_newsletter_linkedin_issue import (
    _Bridge,
    _long_body,
    _patched_settings,
    _request,
    _stub_bridge,
)


class _RaisingHttp:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def post(self, url, **kw):
        raise self.exc


@pytest.mark.asyncio
async def test_topic_mode_forwards_provider_and_model(monkeypatch):
    seen = {}

    async def fake_generate(topic, **kw):
        seen.update(kw)
        return {"title": "", "sections": []}

    monkeypatch.setattr(newsletter_api, "generate_linkedin_article", fake_generate)
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(topic="t", provider="groq", model="llama-x"),
                _request(), db=None,
            )
    # generator returned no title → 422 before any publish attempt
    assert e.value.status_code == 422 and "no title" in e.value.detail
    assert seen["provider"] == "groq" and seen["model"] == "llama-x"


@pytest.mark.asyncio
async def test_sidecar_transport_error_falls_back_to_bridge(monkeypatch):
    monkeypatch.setattr(
        sidecar_mod.httpx, "AsyncClient",
        lambda *a, **k: _RaisingHttp(httpx.ReadTimeout("publish timed out")),
    )
    bridge = _stub_bridge(monkeypatch, _Bridge())
    with _patched_settings():
        resp = await newsletter_api.linkedin_issue(
            newsletter_api.LinkedInIssueRequest(title="Issue", html_body=_long_body(1300)),
            _request(), db=None,
        )
    assert resp.published is True
    assert bridge.calls and bridge.calls[0]["title"] == "Issue"


@pytest.mark.asyncio
async def test_sidecar_only_mode_surfaces_502(monkeypatch):
    monkeypatch.setattr(
        sidecar_mod.httpx, "AsyncClient",
        lambda *a, **k: _RaisingHttp(httpx.ConnectError("refused")),
    )
    bridge = _stub_bridge(monkeypatch, _Bridge())
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.linkedin_issue(
                newsletter_api.LinkedInIssueRequest(
                    title="Issue", html_body=_long_body(1300), browser="sidecar",
                ),
                _request(), db=None,
            )
    assert e.value.status_code == 502 and "ConnectError: refused" in e.value.detail
    assert bridge.calls == []  # no bridge fallback in sidecar-only mode
