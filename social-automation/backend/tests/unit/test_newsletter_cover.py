"""Tests for the newsletter cover renderer + service-token endpoint."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request
from PIL import Image
from pydantic import ValidationError

import app.api.newsletter as newsletter_api
from app.services import newsletter_cover as cover_mod

# ── renderer ─────────────────────────────────────────────────────────


def test_render_returns_png_at_brand_dimensions():
    png = cover_mod.render_newsletter_cover(
        "Workers AI pipelines: shipping the weekly issue", issue_number=7
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (cover_mod.WIDTH, cover_mod.HEIGHT)
    assert img.format == "PNG"


def test_render_is_deterministic():
    a = cover_mod.render_newsletter_cover("Same title", issue_number=3)
    b = cover_mod.render_newsletter_cover("Same title", issue_number=3)
    assert a == b


def test_render_ascii_safe_and_long_title_clamped():
    png = cover_mod.render_newsletter_cover(
        "Em — dash “quotes” and a " + "very long " * 40 + "title"
    )
    img = Image.open(io.BytesIO(png))
    assert img.size == (cover_mod.WIDTH, cover_mod.HEIGHT)  # no crash, clamped


def test_render_empty_title_falls_back():
    png = cover_mod.render_newsletter_cover("   ")
    assert len(png) > 1000


def test_default_issue_date_is_iso():
    from datetime import date as _d

    assert cover_mod.default_issue_date() == _d.today().isoformat()


# ── endpoint (direct calls — version-proof, no TestClient) ───────────


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


@pytest.mark.asyncio
async def test_cover_503_when_service_token_unconfigured():
    with patch.object(
        newsletter_api,
        "get_settings",
        return_value=SimpleNamespace(NEWSLETTER_SERVICE_TOKEN=""),
    ):
        with pytest.raises(HTTPException) as e:
            await newsletter_api.newsletter_cover(
                newsletter_api.NewsletterCoverRequest(title="x"), _request("")
            )
    assert e.value.status_code == 503
    assert "NEWSLETTER_SERVICE_TOKEN" in e.value.detail


@pytest.mark.asyncio
async def test_cover_401_on_missing_or_wrong_token():
    with _patched_settings():
        with pytest.raises(HTTPException) as missing:
            await newsletter_api.newsletter_cover(
                newsletter_api.NewsletterCoverRequest(title="x"), _request("")
            )
        with pytest.raises(HTTPException) as wrong:
            await newsletter_api.newsletter_cover(
                newsletter_api.NewsletterCoverRequest(title="x"), _request("wrong")
            )
    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401


@pytest.mark.asyncio
async def test_cover_200_returns_png():
    with _patched_settings():
        resp = await newsletter_api.newsletter_cover(
            newsletter_api.NewsletterCoverRequest(
                title="Issue title", issue_number=12, category="Cloud"
            ),
            _request(),
        )
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    img = Image.open(io.BytesIO(resp.body))
    assert img.size == (cover_mod.WIDTH, cover_mod.HEIGHT)


@pytest.mark.asyncio
async def test_cover_422_on_bad_issue_date():
    with _patched_settings():
        with pytest.raises(HTTPException) as e:
            await newsletter_api.newsletter_cover(
                newsletter_api.NewsletterCoverRequest(title="x", issue_date="not-a-date"),
                _request(),
            )
    assert e.value.status_code == 422


def test_cover_422_on_blank_title():
    with pytest.raises(ValidationError):
        newsletter_api.NewsletterCoverRequest(title="")


@pytest.mark.asyncio
async def test_cover_uses_today_when_date_omitted(monkeypatch):
    sentinel = "2026-10-12"
    monkeypatch.setattr(newsletter_api, "default_issue_date", lambda: sentinel)
    seen = {}

    def fake_render(title, **kwargs):
        seen["issue_date"] = kwargs.get("issue_date")
        return b"png-bytes"

    with _patched_settings():
        monkeypatch.setattr(newsletter_api, "render_newsletter_cover", fake_render)
        await newsletter_api.newsletter_cover(
            newsletter_api.NewsletterCoverRequest(title="x"), _request()
        )
    assert seen["issue_date"] == sentinel


@pytest.mark.asyncio
async def test_cover_spellcheck_failure_uses_raw_title(monkeypatch):
    """auto_correct blowing up must never block the cover — raw title wins."""
    import app.services.spellcheck as SC

    async def _boom(_text):
        raise RuntimeError("languagetool down")

    monkeypatch.setattr(SC, "auto_correct", _boom)
    with _patched_settings():
        resp = await newsletter_api.newsletter_cover(
            newsletter_api.NewsletterCoverRequest(title="Raw Title"),
            _request(),
        )
    assert resp.status_code == 200
