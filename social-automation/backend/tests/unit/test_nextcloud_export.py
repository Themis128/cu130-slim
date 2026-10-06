"""Unit coverage for the Nextcloud WebDAV export path.

The mirror (services/nextcloud_export.py) is fire-and-forget from the
worker — if it regresses (bad URL quoting, MKCOL mishandling, share API
drift) nothing else fails, so these tests pin the wire behavior.
"""
import urllib.parse
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models.content import MediaAsset
from app.services import nextcloud_export as nc


DAV = "https://cloud.cloudless.gr/remote.php/dav/files/tester"


@pytest.fixture()
def nc_settings(monkeypatch):
    """Point the module-level settings at a test workspace."""
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_EXPORT_ENABLED", True)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DAV_URL", DAV)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_EXPORT_ROOT", "SocialAuto")
    return nc.settings


def _mock_client(monkeypatch, handler):
    """Swap _client() for one backed by an httpx.MockTransport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _factory():
        return client

    monkeypatch.setattr(nc, "_client", _factory)
    return client


@pytest.mark.asyncio
async def test_upload_bytes_creates_dirs_and_puts(monkeypatch, nc_settings):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "MKCOL":
            # Dirs may already exist — 405 must be tolerated.
            return httpx.Response(405 if request.url.path.endswith("/SocialAuto") else 201)
        return httpx.Response(201)

    _mock_client(monkeypatch, handler)
    full = await nc.upload_bytes("media/2026-10-06/hero.png", b"png-bytes", "image/png")

    assert full == "SocialAuto/media/2026-10-06/hero.png"
    methods = [(r.method, r.url.path) for r in requests]
    assert methods.count(("MKCOL", "/remote.php/dav/files/tester/SocialAuto")) == 1
    assert ("MKCOL", "/remote.php/dav/files/tester/SocialAuto/media") in methods
    assert ("MKCOL", "/remote.php/dav/files/tester/SocialAuto/media/2026-10-06") in methods
    put = next(r for r in requests if r.method == "PUT")
    assert put.url.path == "/remote.php/dav/files/tester/SocialAuto/media/2026-10-06/hero.png"
    assert put.headers["Content-Type"] == "image/png"
    assert put.read() == b"png-bytes"


@pytest.mark.asyncio
async def test_upload_bytes_raises_on_failed_put(monkeypatch, nc_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "MKCOL":
            return httpx.Response(405)
        return httpx.Response(500, text="disk full")

    _mock_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await nc.upload_bytes("media/x.png", b"data")


@pytest.mark.asyncio
async def test_create_public_share_read_only(monkeypatch, nc_settings):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = {
            k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()
        }
        return httpx.Response(
            200,
            json={
                "ocs": {
                    "meta": {"status": "ok"},
                    "data": {"url": "https://cloud.cloudless.gr/index.php/s/tok123"},
                }
            },
        )

    _mock_client(monkeypatch, handler)
    url = await nc.create_public_share("media/2026-10-06/hero.png")

    assert url.endswith("/index.php/s/tok123")
    assert seen["headers"].get("ocs-apirequest") == "true"
    # OCS paths are user-root absolute, export root included, read-only.
    assert seen["body"]["path"] == "/SocialAuto/media/2026-10-06/hero.png"
    assert seen["body"]["shareType"] == "3"
    assert seen["body"]["permissions"] == "1"


@pytest.mark.asyncio
async def test_create_public_share_accepts_root_included_path(monkeypatch, nc_settings):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = {
            k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()
        }
        return httpx.Response(200, json={"ocs": {"data": {"url": "https://x/s/y"}}})

    _mock_client(monkeypatch, handler)
    await nc.create_public_share("/SocialAuto/media/dup.png")
    # Root-included paths must not get the root appended twice.
    assert seen["body"]["path"] == "/SocialAuto/media/dup.png"


@pytest.mark.asyncio
async def test_create_public_share_with_password(monkeypatch, nc_settings):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = {
            k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()
        }
        return httpx.Response(200, json={"ocs": {"data": {"url": "https://x/s/z"}}})

    _mock_client(monkeypatch, handler)
    await nc.create_public_share("media/x.png", password="s3cret")
    assert seen["body"]["password"] == "s3cret"


def test_nextcloud_export_enabled_gate(monkeypatch, nc_settings):
    assert nc.nextcloud_export_enabled() is True
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_APP_PASSWORD", "")
    assert nc.nextcloud_export_enabled() is False
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_EXPORT_ENABLED", False)
    assert nc.nextcloud_export_enabled() is False


class _FakeResult:
    def __init__(self, asset):
        self._asset = asset

    def scalar_one_or_none(self):
        return self._asset


class _FakeSession:
    def __init__(self, asset):
        self._asset = asset
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._asset)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_factory(session):
    def factory():
        return session

    return factory


@pytest.mark.asyncio
async def test_export_media_asset_missing_asset_skipped(monkeypatch, nc_settings):
    session = _FakeSession(None)
    result = await nc.export_media_asset(
        "00000000-0000-0000-0000-000000000001", _fake_factory(session)
    )
    assert result == {"status": "skipped", "reason": "asset not found"}


@pytest.mark.asyncio
async def test_export_media_asset_uploads_and_records_path(
    monkeypatch, nc_settings, tmp_path
):
    asset = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000009",
        storage_backend="local",
        storage_path="2026-10-06/hero.png",
        mime_type="image/png",
        meta_data=None,
    )
    (tmp_path / "2026-10-06").mkdir(parents=True)
    (tmp_path / "2026-10-06" / "hero.png").write_bytes(b"local-bytes")
    monkeypatch.setattr(nc, "UPLOAD_DIR", str(tmp_path))

    puts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "MKCOL":
            return httpx.Response(201)
        puts.append(request)
        return httpx.Response(201)

    _mock_client(monkeypatch, handler)
    session = _FakeSession(asset)
    result = await nc.export_media_asset(asset.id, _fake_factory(session))

    assert result == {
        "status": "exported",
        "remote_path": "SocialAuto/media/2026-10-06/hero.png",
        "bytes": len(b"local-bytes"),
    }
    assert asset.meta_data["nextcloud_path"] == "SocialAuto/media/2026-10-06/hero.png"
    assert session.committed is True
    assert puts[0].read() == b"local-bytes"
