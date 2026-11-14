"""Unit tests for media storage integrity verification and sweep behavior."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.user import AuditLog
from app.services import media_integrity


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
