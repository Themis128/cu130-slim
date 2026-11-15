"""Unit tests for media storage integrity verification and sweep behavior."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.user import AuditLog
from app.services import media_integrity, r2_storage


class _Res:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _DB:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Res(self._results.pop(0) if self._results else [])

    def add(self, obj):
        self.added.append(obj)


@pytest.fixture(autouse=True)
def _no_flag_modified(monkeypatch):
    monkeypatch.setattr(media_integrity, "flag_modified", lambda *args, **kwargs: None)


def _asset(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        filename=kw.pop("filename", "asset.mp4"),
        size_bytes=kw.pop("size_bytes", 123),
        storage_backend=kw.pop("storage_backend", "r2"),
        storage_path=kw.pop("storage_path", "team/2026/asset.mp4"),
        public_url=kw.pop("public_url", "https://old.example/object"),
        is_archived=kw.pop("is_archived", False),
        meta_data=kw.pop("meta_data", {}),
        **kw,
    )


@pytest.mark.asyncio
async def test_verify_uploaded_object_uses_declared_backend(monkeypatch):
    calls = []

    async def probe(backend, key):
        calls.append(backend)
        return media_integrity.ObjectProbe(backend=backend, status="ok", size=123, public_url="u")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    out = await media_integrity.verify_uploaded_object("team/file.png", backend="minio", expected_size=123)
    assert out.backend == "minio"
    assert calls == ["minio"]


@pytest.mark.asyncio
async def test_verify_uploaded_object_requires_declared_backend(monkeypatch):
    calls = []

    async def probe(backend, key):
        calls.append(backend)
        if backend == "minio":
            return media_integrity.ObjectProbe(backend=backend, status="ok", size=123, public_url="/view")
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    with pytest.raises(HTTPException) as exc:
        await media_integrity.verify_uploaded_object("team/file.png", backend="r2", expected_size=123)
    assert exc.value.status_code == 400
    assert calls == ["r2"]


@pytest.mark.asyncio
async def test_verify_uploaded_object_finds_backend_for_legacy_client(monkeypatch):
    async def probe(backend, key):
        if backend == "minio":
            return media_integrity.ObjectProbe(backend=backend, status="ok", size=123, public_url="/view")
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    out = await media_integrity.verify_uploaded_object("team/file.png", backend=None, expected_size=123)
    assert out.backend == "minio"


@pytest.mark.asyncio
async def test_verify_uploaded_object_rejects_missing(monkeypatch):
    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    with pytest.raises(HTTPException) as exc:
        await media_integrity.verify_uploaded_object("team/file.png", backend="r2", expected_size=123)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verify_uploaded_object_rejects_size_mismatch(monkeypatch):
    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="ok", size=999)

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    with pytest.raises(HTTPException) as exc:
        await media_integrity.verify_uploaded_object("team/file.png", backend="r2", expected_size=123)
    assert exc.value.status_code == 400
    assert "size" in exc.value.detail


@pytest.mark.asyncio
async def test_verify_uploaded_object_502_when_storage_unavailable(monkeypatch):
    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="unavailable", error="down")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    with pytest.raises(HTTPException) as exc:
        await media_integrity.verify_uploaded_object("team/file.png", backend="r2", expected_size=123)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_inspect_repairs_backend_label(monkeypatch):
    async def probe(backend, key):
        if backend == "minio":
            return media_integrity.ObjectProbe(
                backend=backend,
                status="ok",
                size=123,
                public_url="/api/v1/media/view?path=team%2F2026%2Fasset.mp4",
            )
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    asset = _asset(storage_backend="r2", public_url="https://old.example/object")
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=False)

    assert out["status"] == "repaired"
    assert asset.storage_backend == "minio"
    assert asset.public_url.startswith("/api/v1/media/view")
    integrity = asset.meta_data["storage_integrity"]
    assert integrity["status"] == "ok"
    assert integrity["last_repair"]["from_backend"] == "r2"
    assert integrity["last_repair"]["to_backend"] == "minio"


@pytest.mark.asyncio
async def test_inspect_quarantines_missing_without_delete(monkeypatch):
    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    asset = _asset()
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=True)

    assert out["status"] == "missing"
    assert out["newly_unresolved"] is True
    assert asset.is_archived is True
    integrity = asset.meta_data["storage_integrity"]
    assert integrity["status"] == "missing"
    assert integrity["auto_archived"] is True
    assert integrity["referenced"] is True


@pytest.mark.asyncio
async def test_inspect_quarantines_size_mismatch(monkeypatch):
    async def probe(backend, key):
        if backend == "r2":
            return media_integrity.ObjectProbe(backend=backend, status="ok", size=999)
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    asset = _asset(size_bytes=123)
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=False)

    assert out["status"] == "size_mismatch"
    assert asset.is_archived is True
    assert asset.meta_data["storage_integrity"]["status"] == "size_mismatch"


@pytest.mark.asyncio
async def test_sweep_writes_quiet_audit_and_uses_bound(monkeypatch):
    team_id = uuid.uuid4()
    asset = _asset(team_id=team_id)
    db = _DB([([], None)], [asset])

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="ok", size=asset.size_bytes, public_url=asset.public_url)

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    summary = await media_integrity.run_integrity_sweep(db, team_id, batch_size=1, notify=False)

    assert summary["checked"] == 1
    assert summary["ok"] == 1
    assert isinstance(db.added[0], AuditLog)
    assert db.added[0].action == "media-integrity-sweep"
    assert 1 in db.statements[1].compile().params.values()


@pytest.mark.asyncio
async def test_sweep_alerts_once_for_same_missing_asset(monkeypatch):
    team_id = uuid.uuid4()
    asset = _asset(team_id=team_id)
    db = _DB([([asset.id], None)], [asset], [([asset.id], None)], [asset])
    alert = AsyncMock()

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    monkeypatch.setitem(__import__("sys").modules, "app.services.slack_notifications", SimpleNamespace(post_alert_to_slack=alert))

    first = await media_integrity.run_integrity_sweep(db, team_id, notify=True)
    second = await media_integrity.run_integrity_sweep(db, team_id, notify=True)

    assert first["missing"] == 1
    assert first["newly_unresolved"] == 1
    assert second["missing"] == 1
    assert second["newly_unresolved"] == 0
    assert alert.await_count == 1
    assert len(db.added) == 2


@pytest.mark.asyncio
async def test_sweep_failed_alert_does_not_suppress_retry(monkeypatch):
    team_id = uuid.uuid4()
    asset = _asset(team_id=team_id)
    db = _DB([([], None)], [asset], [([], None)], [asset])
    alert = AsyncMock(return_value=False)

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    monkeypatch.setitem(__import__("sys").modules, "app.services.slack_notifications", SimpleNamespace(post_alert_to_slack=alert))

    first = await media_integrity.run_integrity_sweep(db, team_id, notify=True)
    second = await media_integrity.run_integrity_sweep(db, team_id, notify=True)

    assert first["newly_unresolved"] == 1
    assert second["newly_unresolved"] == 1
    assert alert.await_count == 2


@pytest.mark.asyncio
async def test_r2_head_object_uses_s3_metadata(monkeypatch):
    calls = []

    class _Client:
        def head_object(self, bucket, key):
            calls.append((bucket, key))
            return {
                "Content-Length": "123",
                "ETag": '"abc"',
                "Content-Type": "video/mp4",
                "Last-Modified": "Fri, 14 Nov 2026 00:00:00 GMT",
            }

    monkeypatch.setattr(r2_storage, "_r2_s3_client", lambda: _Client())
    monkeypatch.setattr(r2_storage.settings, "R2_BUCKET_NAME", "media-bucket")

    out = await r2_storage.head_object("team/2026/asset.mp4")

    assert calls == [("media-bucket", "team/2026/asset.mp4")]
    assert out["size"] == 123
    assert out["etag"] == "abc"
    assert out["content_type"] == "video/mp4"


@pytest.mark.asyncio
async def test_r2_head_object_preserves_zero_byte_size(monkeypatch):
    class _Client:
        def head_object(self, bucket, key):
            return {"Content-Length": "0", "ETag": '"empty"'}

    monkeypatch.setattr(r2_storage, "_r2_s3_client", lambda: _Client())
    monkeypatch.setattr(r2_storage.settings, "R2_BUCKET_NAME", "media-bucket")

    out = await r2_storage.head_object("team/2026/empty.mp4")

    assert out["size"] == 0
    assert out["etag"] == "empty"

# ── probe_object / _local_probe / _public_url_for (real, unmocked) ──────


@pytest.mark.asyncio
async def test_probe_object_local_ok_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(media_integrity, "UPLOAD_DIR", str(tmp_path))
    (tmp_path / "a.txt").write_bytes(b"12345")

    ok = await media_integrity.probe_object("local", "a.txt")
    assert ok.status == "ok" and ok.size == 5 and ok.backend == media_integrity.StorageBackend.local
    assert ok.public_url and "a.txt" in ok.public_url

    miss = await media_integrity.probe_object(media_integrity.StorageBackend.local, "nope.txt")
    assert miss.status == "missing"


@pytest.mark.asyncio
async def test_probe_object_unsupported_backend():
    out = await media_integrity.probe_object("bogus", "k")
    assert out.status == "missing" and "unsupported backend" in (out.error or "")


@pytest.mark.asyncio
async def test_probe_object_r2_ok_and_http_errors(monkeypatch):
    head = AsyncMock(return_value={"size": 42})
    monkeypatch.setattr(r2_storage, "head_object", head)
    monkeypatch.setattr(media_integrity.r2_presigned, "_public_url", lambda k: f"https://pub/{k}")

    ok = await media_integrity.probe_object("r2", "team/x.png")
    assert ok.status == "ok" and ok.size == 42
    assert ok.public_url

    head.side_effect = HTTPException(status_code=404, detail="gone")
    miss = await media_integrity.probe_object("r2", "team/x.png")
    assert miss.status == "missing"

    head.side_effect = HTTPException(status_code=500, detail="boom")
    unavail = await media_integrity.probe_object("r2", "team/x.png")
    assert unavail.status == "unavailable"

    head.side_effect = RuntimeError("net down")
    err = await media_integrity.probe_object("r2", "team/x.png")
    assert err.status == "unavailable" and "net down" in (err.error or "")


@pytest.mark.asyncio
async def test_probe_object_minio_ok(monkeypatch):
    from app.services import minio_storage

    head = AsyncMock(return_value={"size": 7})
    monkeypatch.setattr(minio_storage, "head_object", head)

    ok = await media_integrity.probe_object("minio", "team/y.jpg")
    assert ok.status == "ok" and ok.size == 7
    head.assert_awaited_once_with("team/y.jpg")

@pytest.mark.asyncio
async def test_probe_object_local_path_escape_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(media_integrity, "UPLOAD_DIR", str(tmp_path))
    out = await media_integrity.probe_object("local", "../escape.txt")
    assert out.status == "missing"


@pytest.mark.asyncio
async def test_probe_object_local_public_url_relative_without_base(tmp_path, monkeypatch):
    monkeypatch.setattr(media_integrity, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media_integrity.settings, "MEDIA_PUBLIC_BASE_URL", "", raising=False)
    (tmp_path / "b.txt").write_bytes(b"x")
    out = await media_integrity.probe_object("local", "b.txt")
    assert out.public_url == "/api/v1/media/view?path=b.txt"


@pytest.mark.asyncio
async def test_verify_uploaded_object_rejects_unsupported_declared_backend():
    with pytest.raises(HTTPException) as exc:
        await media_integrity.verify_uploaded_object("team/file.png", backend="bogus")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_inspect_backend_error_when_storage_unavailable(monkeypatch):
    asset = _asset(storage_backend="r2")

    async def probe(backend, key):
        # declared r2 probe fails hard; minio also unavailable -> synthetic
        # "unavailable" declared probe -> backend_error result branch.
        return media_integrity.ObjectProbe(backend=backend, status="unavailable", error="down")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=False)
    assert out["status"] == "backend_error"
    assert out["newly_backend_error"] is True
    assert out.get("error")


@pytest.mark.asyncio
async def test_inspect_backend_error_repeat_is_not_new(monkeypatch):
    asset = _asset(
        storage_backend="r2",
        meta_data={
            "storage_integrity": {
                "status": "backend_error",
                "alerted_at": "2026-01-01T00:00:00+00:00",
            }
        },
    )

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="unavailable", error="down")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=False)
    assert out["status"] == "backend_error"
    assert out["newly_backend_error"] is False


@pytest.mark.asyncio
async def test_sweep_counts_repaired_and_referenced_missing(monkeypatch):
    team_id = uuid.uuid4()
    repaired_asset = _asset(id=uuid.uuid4(), team_id=team_id, storage_backend="minio",
                            public_url="https://old.example/object")
    missing_asset = _asset(id=uuid.uuid4(), team_id=team_id, filename="gone.png",
                           storage_path="team/2026/gone.png")
    db = _DB([([missing_asset.id], None)], [repaired_asset, missing_asset])

    async def probe(backend, key):
        if key == missing_asset.storage_path:
            return media_integrity.ObjectProbe(backend=backend, status="missing")
        # repaired asset: declared minio misses, r2 has it -> repair label
        if backend == "r2":
            return media_integrity.ObjectProbe(backend=backend, status="ok", size=repaired_asset.size_bytes,
                                               public_url="https://pub/x")
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    monkeypatch.setattr(media_integrity, "StorageBackend", media_integrity.StorageBackend)
    summary = await media_integrity.run_integrity_sweep(db, team_id, notify=False)

    assert summary["repaired"] == 1
    assert summary["repaired_assets"] == [str(repaired_asset.id)]
    assert summary["missing"] == 1
    assert summary["referenced_unresolved"] == 1
    assert summary["unresolved_assets"][0]["id"] == str(missing_asset.id)
    assert missing_asset.is_archived is True

@pytest.mark.asyncio
async def test_probe_object_local_public_url_with_base(tmp_path, monkeypatch):
    monkeypatch.setattr(media_integrity, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media_integrity.settings, "MEDIA_PUBLIC_BASE_URL", "https://cdn.example", raising=False)
    (tmp_path / "c.txt").write_bytes(b"x")
    out = await media_integrity.probe_object("local", "c.txt")
    assert out.public_url == "https://cdn.example/api/v1/media/view?path=c.txt"


@pytest.mark.asyncio
async def test_local_probe_generic_exception_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(media_integrity, "UPLOAD_DIR", str(tmp_path))

    class _BadPath:
        def is_file(self):
            raise RuntimeError("io error")

    monkeypatch.setattr(media_integrity, "safe_resolve", lambda *a, **k: _BadPath())
    out = await media_integrity.probe_object("local", "x.bin")
    assert out.status == "unavailable"


@pytest.mark.asyncio
async def test_inspect_unarchives_previously_quarantined_asset(monkeypatch):
    asset = _asset(
        is_archived=True,
        meta_data={"storage_integrity": {"auto_archived": True, "status": "missing"}},
    )

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="ok", size=asset.size_bytes,
                                           public_url=asset.public_url)

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    out = await media_integrity.inspect_and_repair_asset(asset, referenced=False)
    assert out["status"] == "ok"
    assert out["unarchived"] is True
    assert asset.is_archived is False


@pytest.mark.asyncio
async def test_sweep_counts_backend_error_asset(monkeypatch):
    team_id = uuid.uuid4()
    asset = _asset(team_id=team_id, storage_backend="r2")
    db = _DB([([], None)], [asset])

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="unavailable", error="down")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    summary = await media_integrity.run_integrity_sweep(db, team_id, notify=False)
    assert summary["backend_errors"] == 1
    assert summary["newly_backend_errors"] == 1
    assert summary["backend_error_assets"][0]["id"] == str(asset.id)
    assert summary["backend_error_assets"][0]["error"]


@pytest.mark.asyncio
async def test_sweep_alert_failure_is_swallowed(monkeypatch):
    team_id = uuid.uuid4()
    asset = _asset(team_id=team_id)
    db = _DB([([], None)], [asset])

    async def probe(backend, key):
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    async def boom(*a, **k):
        raise RuntimeError("slack down")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    monkeypatch.setitem(__import__("sys").modules, "app.services.slack_notifications",
                        SimpleNamespace(post_alert_to_slack=boom))
    summary = await media_integrity.run_integrity_sweep(db, team_id, notify=True)
    assert summary["newly_unresolved"] == 1


@pytest.mark.asyncio
async def test_sweep_alert_marks_alerted_at_only_for_unresolved(monkeypatch):
    team_id = uuid.uuid4()
    ok_asset = _asset(id=uuid.uuid4(), team_id=team_id, storage_path="team/ok.png")
    bad_asset = _asset(id=uuid.uuid4(), team_id=team_id, storage_path="team/bad.png")
    db = _DB([([], None)], [ok_asset, bad_asset])
    alert = AsyncMock(return_value=True)

    async def probe(backend, key):
        if key == "team/bad.png":
            return media_integrity.ObjectProbe(backend=backend, status="missing")
        return media_integrity.ObjectProbe(backend=backend, status="ok", size=ok_asset.size_bytes,
                                           public_url=ok_asset.public_url)

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    monkeypatch.setitem(__import__("sys").modules, "app.services.slack_notifications",
                        SimpleNamespace(post_alert_to_slack=alert))
    summary = await media_integrity.run_integrity_sweep(db, team_id, notify=True)
    assert summary["newly_unresolved"] == 1
    bad_integrity = bad_asset.meta_data["storage_integrity"]
    assert bad_integrity.get("alerted_at")
    assert not ok_asset.meta_data["storage_integrity"].get("alerted_at")


@pytest.mark.asyncio
async def test_sweep_sample_lists_cap_at_25(monkeypatch):
    """>25 same-status assets trip the ``len(...) < 25`` false arcs."""
    team_id = uuid.uuid4()
    assets = []
    kinds = {}
    for i in range(26):
        a = _asset(id=uuid.uuid4(), team_id=team_id, storage_backend="minio",
                   storage_path=f"team/r{i}.png", public_url="https://old/x")
        kinds[a.storage_path] = "repaired"
        assets.append(a)
    for i in range(26):
        a = _asset(id=uuid.uuid4(), team_id=team_id, storage_backend="local",
                   storage_path=f"team/m{i}.png")
        kinds[a.storage_path] = "missing"
        assets.append(a)
    for i in range(26):
        a = _asset(id=uuid.uuid4(), team_id=team_id, storage_backend="r2",
                   storage_path=f"team/e{i}.png")
        kinds[a.storage_path] = "backend_error"
        assets.append(a)
    db = _DB([([], None)], assets)

    async def probe(backend, key):
        kind = kinds[key]
        if kind == "repaired":
            if backend == "r2":
                return media_integrity.ObjectProbe(backend=backend, status="ok", size=123,
                                                   public_url="https://pub/x")
            return media_integrity.ObjectProbe(backend=backend, status="missing")
        if kind == "missing":
            return media_integrity.ObjectProbe(backend=backend, status="missing")
        if backend == "r2":
            return media_integrity.ObjectProbe(backend=backend, status="unavailable", error="down")
        return media_integrity.ObjectProbe(backend=backend, status="missing")

    monkeypatch.setattr(media_integrity, "probe_object", probe)
    summary = await media_integrity.run_integrity_sweep(db, team_id, notify=False)
    assert summary["repaired"] == 26
    assert summary["missing"] == 26
    assert summary["backend_errors"] == 26
    assert len(summary["repaired_assets"]) == 25
    assert len(summary["unresolved_assets"]) == 25
    assert len(summary["backend_error_assets"]) == 25
