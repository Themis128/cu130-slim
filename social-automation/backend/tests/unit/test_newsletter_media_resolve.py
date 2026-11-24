"""Tests for newsletter_publisher.resolve_media_items — the media spec
resolver, especially the SSRF allowlist on ``{type, url}`` items."""

from __future__ import annotations

from types import SimpleNamespace

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
    out = await pub.resolve_media_items(None, [{"type": "image", "url": "https://pub-abc.r2.dev/x.png"}])
    assert out == []


@pytest.mark.asyncio
async def test_resolve_base64_item_unaffected(media_origins):
    out = await pub.resolve_media_items(None, [{"type": "image", "base64": "aGk=", "filename": "a.png"}])
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


@pytest.mark.asyncio
async def test_resolve_skips_non_dict_items(media_origins):
    out = await pub.resolve_media_items(None, ["not-a-dict", {"type": "image", "base64": "aGk="}])
    assert len(out) == 1 and out[0]["base64"] == "aGk="


def test_media_url_allowed_valueerror_on_url(media_origins):
    # urlsplit raises ValueError on unmatched IPv6 brackets — treated as reject.
    assert not pub._media_url_allowed("http://[::1")


def test_media_url_allowed_valueerror_on_base(monkeypatch):
    # A malformed configured base is skipped, not fatal.
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "http://[::1")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", "https://pub-abc.r2.dev")
    assert pub._media_url_allowed("https://pub-abc.r2.dev/x.png")


@pytest.mark.asyncio
async def test_resolve_asset_item_loads_bytes(monkeypatch, media_origins):
    from tests.unit.test_newsletter_issue_queue import _FakeDB, _FakeResult

    asset = SimpleNamespace(id="a1", filename="hero.png")
    db = _FakeDB(results=[_FakeResult(obj=asset)])
    monkeypatch.setattr(
        "app.services.media_dedup.load_asset_bytes",
        lambda a: _async_ret(b"\x89PNG"),
    )
    out = await pub.resolve_media_items(db, [{"type": "image", "asset_id": "a1"}])
    assert len(out) == 1
    assert out[0]["base64"] == "iVBORw=="
    assert out[0]["filename"] == "hero.png"


@pytest.mark.asyncio
async def test_resolve_asset_item_missing_is_skipped(media_origins):
    from tests.unit.test_newsletter_issue_queue import _FakeDB, _FakeResult

    db = _FakeDB(results=[_FakeResult(obj=None)])
    out = await pub.resolve_media_items(db, [{"type": "image", "asset_id": "ghost"}])
    assert out == []


@pytest.mark.asyncio
async def test_resolve_asset_item_load_failure_is_skipped(monkeypatch, media_origins):
    from tests.unit.test_newsletter_issue_queue import _FakeDB, _FakeResult

    asset = SimpleNamespace(id="a2", filename="reel.mp4")
    db = _FakeDB(results=[_FakeResult(obj=asset)])

    async def boom(a):
        raise RuntimeError("r2 down")

    monkeypatch.setattr("app.services.media_dedup.load_asset_bytes", boom)
    out = await pub.resolve_media_items(db, [{"type": "video", "asset_id": "a2"}])
    assert out == []


@pytest.mark.asyncio
async def test_resolve_asset_item_ignored_without_db(media_origins):
    out = await pub.resolve_media_items(None, [{"type": "image", "asset_id": "a1"}])
    assert out == []


async def _async_ret(v):
    return v
