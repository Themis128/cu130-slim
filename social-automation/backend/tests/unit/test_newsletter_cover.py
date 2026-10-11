"""Tests for the newsletter cover renderer + service-token endpoint."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

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


# ── endpoint ─────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(newsletter_api.router)
    with patch.object(
        newsletter_api, "get_settings", return_value=SimpleNamespace(SOCIALAUTO_SERVICE_TOKEN="s3cret")
    ):
        yield TestClient(app)


def test_cover_503_when_service_token_unconfigured():
    app = FastAPI()
    app.include_router(newsletter_api.router)
    with patch.object(
        newsletter_api, "get_settings", return_value=SimpleNamespace(SOCIALAUTO_SERVICE_TOKEN="")
    ):
        client = TestClient(app)
        r = client.post("/newsletter/cover", json={"title": "x"})
    assert r.status_code == 503
    assert "SOCIALAUTO_SERVICE_TOKEN" in r.json()["detail"]


def test_cover_401_on_missing_or_wrong_token(client):
    assert client.post("/newsletter/cover", json={"title": "x"}).status_code == 401
    r = client.post(
        "/newsletter/cover", json={"title": "x"}, headers={"X-Service-Token": "wrong"}
    )
    assert r.status_code == 401


def test_cover_200_returns_png(client):
    r = client.post(
        "/newsletter/cover",
        json={"title": "Issue title", "issue_number": 12, "category": "Cloud"},
        headers={"X-Service-Token": "s3cret"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=86400"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (cover_mod.WIDTH, cover_mod.HEIGHT)


def test_cover_422_on_bad_issue_date(client):
    r = client.post(
        "/newsletter/cover",
        json={"title": "x", "issue_date": "not-a-date"},
        headers={"X-Service-Token": "s3cret"},
    )
    assert r.status_code == 422


def test_cover_422_on_blank_title(client):
    r = client.post("/newsletter/cover", json={"title": ""})
    assert r.status_code == 422


def test_cover_uses_today_when_date_omitted(client, monkeypatch):
    sentinel = "2026-10-12"
    monkeypatch.setattr(newsletter_api, "default_issue_date", lambda: sentinel)
    seen = {}

    def fake_render(title, *, issue_number=None, issue_date=None, category=None):
        seen["issue_date"] = issue_date
        return b"\x89PNG fake"

    monkeypatch.setattr(newsletter_api, "render_newsletter_cover", fake_render)
    r = client.post(
        "/newsletter/cover", json={"title": "x"}, headers={"X-Service-Token": "s3cret"}
    )
    assert r.status_code == 200
    assert seen["issue_date"] == sentinel
