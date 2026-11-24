"""Tests for newsletter_publisher.resolve_media_items — the media spec
resolver, especially the SSRF allowlist on ``{type, url}`` items."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import newsletter_publisher as pub


@pytest.fixture
def media_origins(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "https://media.cloudless.gr")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://pub-abc.r2.dev/")


@pytest.mark.asyncio
async def test_resolve_url_item_allowed_origin(media_origins):
    out = await pub.resolve_media_items(
        None,
        [{"type": "image", "url": "https://pub-abc.r2.dev/newsletter/pic.png", "filename": "pic.png"}],
    )
    assert out == [
        {
            "type": "image",
            "base64": None,
            "url": "https://pub-abc.r2.dev/newsletter/pic.png",
            "filename": "pic.png",
        }
    ]


@pytest.mark.asyncio
async def test_resolve_url_item_media_base_origin(media_origins):
    out = await pub.resolve_media_items(
        None,
        [{"type": "video", "url": "https://media.cloudless.gr/api/v1/media/view?path=x.mp4"}],
    )
    assert len(out) == 1 and out[0]["type"] == "video"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://localhost:8083/api/v1/secrets",
        "https://internal.corp/admin",
        "ftp://pub-abc.r2.dev/x.png",  # bad scheme
        "not-a-url",
        "https://evil.example.com/x.png",
    ],
)
async def test_resolve_url_item_rejected_origins(media_origins, url):
    out = await pub.resolve_media_items(None, [{"type": "image", "url": url}])
    assert out == []


@pytest.mark.asyncio
async def test_resolve_url_item_dropped_when_no_origins_configured(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "")
    out = await pub.resolve_media_items(
        None, [{"type": "image", "url": "https://pub-abc.r2.dev/x.png"}]
    )
    assert out == []


@pytest.mark.asyncio
async def test_resolve_base64_item_unaffected(media_origins):
    out = await pub.resolve_media_items(
        None, [{"type": "image", "base64": "aGk=", "filename": "a.png"}]
    )
    assert out[0]["base64"] == "aGk=" and out[0]["url"] is None


@pytest.mark.asyncio
async def test_media_url_allowed_unit(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "https://media.cloudless.gr/")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "")
    assert pub._media_url_allowed("https://media.cloudless.gr/x.png")
    assert not pub._media_url_allowed("https://media.cloudless.gr.evil.com/x")
    assert not pub._media_url_allowed("http://media.cloudless.gr/x")  # scheme mismatch
    assert not pub._media_url_allowed("https://media.cloudless.gr:8443/x")
    assert not pub._media_url_allowed("")
