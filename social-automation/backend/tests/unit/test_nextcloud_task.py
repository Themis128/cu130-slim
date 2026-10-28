"""Coverage for the Nextcloud worker task + remaining export-service branches.

cloud.cloudless.gr is upstream Nextcloud (not our code) — this suite covers
our integration surface: the Celery task gate/uuid/exception paths,
R2/MinIO asset reads, MKCOL failure handling, URL quoting, OCS share
derivation, and the media_storage enqueue helper.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import media_storage as ms
from app.services import nextcloud_export as nc
from app.worker.tasks import nextcloud as task

DAV = "https://cloud.cloudless.gr/remote.php/dav/files/tester"


@pytest.fixture()
def nc_settings(monkeypatch):
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_EXPORT_ENABLED", True)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DAV_URL", DAV)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_EXPORT_ROOT", "SocialAuto")
    return nc.settings


# ── worker task ───────────────────────────────────────────────────────


def test_task_disabled_gate(monkeypatch):
    monkeypatch.setattr(task.nextcloud_export, "nextcloud_export_enabled", lambda: False)
    called = []
    monkeypatch.setattr(task, "run_async", lambda c: called.append(c))
    task.export_media_to_nextcloud(str(uuid.uuid4()))
    assert called == []


def test_task_invalid_uuid(monkeypatch, caplog):
    monkeypatch.setattr(task.nextcloud_export, "nextcloud_export_enabled", lambda: True)
    task.export_media_to_nextcloud("not-a-uuid")
    assert "invalid asset_id" in caplog.text


def test_task_happy_path(monkeypatch):
    monkeypatch.setattr(task.nextcloud_export, "nextcloud_export_enabled", lambda: True)
    exported = []
    monkeypatch.setattr(task.nextcloud_export, "export_media_asset", AsyncMock(side_effect=lambda aid, sf: exported.append(aid)))
    monkeypatch.setattr(task, "get_session_factory", lambda: object())
    monkeypatch.setattr(task, "run_async", lambda coro: coro.close() or None)

    # run_async is patched to just close the coro — verify wiring instead
    # by letting it actually run via asyncio
    import asyncio

    monkeypatch.setattr(task, "run_async", lambda coro: asyncio.new_event_loop().run_until_complete(coro))
    aid = str(uuid.uuid4())
    task.export_media_to_nextcloud(aid)
    assert exported and str(exported[0]) == aid


def test_task_exception_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(task.nextcloud_export, "nextcloud_export_enabled", lambda: True)
    monkeypatch.setattr(task, "get_session_factory", lambda: object())

    def _boom(coro):
        coro.close()
        raise RuntimeError("dav down")

    monkeypatch.setattr(task, "run_async", _boom)
    aid = str(uuid.uuid4())
    task.export_media_to_nextcloud(aid)  # must not raise
    assert "nextcloud export failed" in caplog.text


# ── _read_asset_bytes backends ────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_asset_bytes_r2(monkeypatch, nc_settings):
    asset = SimpleNamespace(storage_backend=nc.StorageBackend.r2, storage_path="k/x.png")
    monkeypatch.setattr(nc.r2_storage, "get_object", AsyncMock(return_value=b"r2bytes"))
    assert await nc._read_asset_bytes(asset) == b"r2bytes"
    nc.r2_storage.get_object.assert_awaited_with("k/x.png")


@pytest.mark.asyncio
async def test_read_asset_bytes_minio(monkeypatch, nc_settings):
    asset = SimpleNamespace(storage_backend=nc.StorageBackend.minio, storage_path="b/y.png")
    monkeypatch.setattr(nc.minio_storage, "get_object", AsyncMock(return_value=b"miniobytes"))
    assert await nc._read_asset_bytes(asset) == b"miniobytes"


@pytest.mark.asyncio
async def test_read_asset_bytes_local_missing(monkeypatch, nc_settings, tmp_path):
    monkeypatch.setattr(nc, "UPLOAD_DIR", str(tmp_path))
    asset = SimpleNamespace(storage_backend="local", storage_path="gone.png")
    with pytest.raises(FileNotFoundError):
        await nc._read_asset_bytes(asset)


# ── _ensure_dirs / _q / share edge cases ──────────────────────────────


@pytest.mark.asyncio
async def test_ensure_dirs_unexpected_status_raises(monkeypatch, nc_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(nc, "_client", lambda: client)
    with pytest.raises(httpx.HTTPStatusError):
        await nc.upload_bytes("media/x.png", b"d")


def test_q_url_quoting():
    assert nc._q("a b/c#d.png") == "a%20b/c%23d.png"
    assert nc._q("/media/") == "media"


@pytest.mark.asyncio
async def test_share_no_url_in_response(monkeypatch, nc_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ocs": {"data": {}}})

    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await nc.create_public_share("media/x.png") is None


@pytest.mark.asyncio
async def test_share_ocs_url_derivation(monkeypatch, nc_settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ocs": {"data": {"url": "u"}}})

    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await nc.create_public_share("x.png")
    assert seen["url"] == ("https://cloud.cloudless.gr/ocs/v2.php/apps/files_sharing/api/v1/shares")


@pytest.mark.asyncio
async def test_share_for_recorded_root_only(monkeypatch, nc_settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import urllib.parse

        seen["body"] = {k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"ocs": {"data": {"url": "u"}}})

    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # sharing the export root itself is allowed (folder share)
    await nc.create_share_for_recorded("SocialAuto")
    assert seen["body"]["path"] == "/SocialAuto"


# ── export_media_asset with remote backends ───────────────────────────


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

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_export_media_asset_r2_backend(monkeypatch, nc_settings):
    asset = SimpleNamespace(id=uuid.uuid4(), storage_backend=nc.StorageBackend.r2, storage_path="2026/10/hero.png", mime_type="image/png", meta_data={})
    monkeypatch.setattr(nc.r2_storage, "get_object", AsyncMock(return_value=b"r2img"))

    puts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            puts.append(request)
        return httpx.Response(201)

    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    session = _FakeSession(asset)
    out = await nc.export_media_asset(asset.id, lambda: session)
    assert out["status"] == "exported"
    assert asset.meta_data["nextcloud_path"] == "SocialAuto/media/2026/10/hero.png"
    assert puts[0].read() == b"r2img"


# ── media_storage._enqueue_nextcloud_export ───────────────────────────


def test_enqueue_disabled_no_task(monkeypatch):
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_EXPORT_ENABLED", False)
    sent = []
    monkeypatch.setattr(ms.celery_app, "send_task", lambda *a, **k: sent.append((a, k)))
    ms._enqueue_nextcloud_export(SimpleNamespace(id=uuid.uuid4()))
    assert sent == []


def test_enqueue_enabled_sends_task(monkeypatch):
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_EXPORT_ENABLED", True)
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_DAV_URL", DAV)
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_APP_PASSWORD", "pw")
    sent = []
    monkeypatch.setattr(ms.celery_app, "send_task", lambda *a, **k: sent.append((a, k)))
    aid = uuid.uuid4()
    ms._enqueue_nextcloud_export(SimpleNamespace(id=aid))
    assert sent[0][0][0] == "app.worker.tasks.nextcloud.export_media_to_nextcloud"
    assert sent[0][1]["args"] == [str(aid)]


def test_enqueue_send_failure_swallowed(monkeypatch):
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_EXPORT_ENABLED", True)
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_DAV_URL", DAV)
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(ms.settings, "NEXTCLOUD_APP_PASSWORD", "pw")

    def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ms.celery_app, "send_task", _boom)
    ms._enqueue_nextcloud_export(SimpleNamespace(id=uuid.uuid4()))  # no raise
