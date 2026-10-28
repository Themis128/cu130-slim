"""Unit tests for app/worker/tasks/media_enhance.py — batch enhance task.

Storage backends, transform services, and DB session are faked at module
boundaries; the dispatch loop and per-operation plumbing run for real.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.media_enhance as ME


class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v


class _DB:
    def __init__(self, assets):
        self.assets = assets
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return _Scalar(self.assets.pop(0) if self.assets else None)

    async def commit(self):
        self.commits += 1


def _asset(**kw):
    base = {"id": uuid.uuid4(), "team_id": uuid.uuid4(), "user_id": uuid.uuid4(),
            "storage_backend": "local", "storage_path": "f.png", "alt_text": None}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def deps(monkeypatch):
    sync = AsyncMock()
    persist = AsyncMock()
    monkeypatch.setattr(ME, "sync_after_worker_task", sync)
    return {"sync": sync, "persist": persist}


@pytest.mark.asyncio
async def test_load_asset_bytes_backends(monkeypatch, tmp_path):
    monkeypatch.setattr(ME.r2_storage, "get_object", AsyncMock(return_value=b"r2"))
    monkeypatch.setattr(ME.minio_storage, "get_object", AsyncMock(return_value=b"mn"))
    assert await ME._load_asset_bytes(_asset(storage_backend="r2")) == b"r2"
    assert await ME._load_asset_bytes(_asset(storage_backend="minio")) == b"mn"


@pytest.mark.asyncio
async def test_load_asset_bytes_local(monkeypatch, tmp_path):
    (tmp_path / "f.png").write_bytes(b"local-bytes")
    monkeypatch.setattr(ME, "UPLOAD_DIR", str(tmp_path))
    out = await ME._load_asset_bytes(_asset(storage_backend="local", storage_path="f.png"))
    assert out == b"local-bytes"


@pytest.mark.asyncio
async def test_run_batch_asset_not_found(deps, monkeypatch):
    monkeypatch.setattr(ME, "task_session", lambda: _DB([None]))
    await ME._run_batch([str(uuid.uuid4())], "resize", {})
    deps["sync"].assert_awaited_once_with(["media_assets"])


@pytest.mark.asyncio
async def test_run_batch_resize_persists(deps, monkeypatch):
    asset = _asset()
    monkeypatch.setattr(ME, "task_session", lambda: _DB([asset]))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_transform, "resize_image",
                        lambda b, **kw: SimpleNamespace(image_bytes=b"out", mime_type="image/webp"))
    import app.services.media_storage as ms
    monkeypatch.setattr(ms, "persist_generated_image", deps["persist"])
    await ME._run_batch([str(asset.id)], "resize", {"width": 100})
    deps["persist"].assert_awaited_once()
    kw = deps["persist"].await_args.kwargs
    assert kw["image_bytes"] == b"out" and kw["extension"] == ".webp"
    assert kw["prompt"] == "batch_resize" and kw["source"] == "ai-enhanced"


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["convert", "compress"])
async def test_run_batch_transform_ops(deps, monkeypatch, op):
    asset = _asset()
    monkeypatch.setattr(ME, "task_session", lambda: _DB([asset]))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_transform, f"{op}_{'format' if op=='convert' else 'image'}",
                        lambda b, **kw: SimpleNamespace(image_bytes=b"o", mime_type="image/png"))
    import app.services.media_storage as ms
    monkeypatch.setattr(ms, "persist_generated_image", deps["persist"])
    await ME._run_batch([str(asset.id)], op, {})
    deps["persist"].assert_awaited_once()
    assert deps["persist"].await_args.kwargs["extension"] == ".png"


@pytest.mark.asyncio
async def test_run_batch_upscale_and_bg(deps, monkeypatch):
    a1, a2 = _asset(), _asset()
    dbs = iter([_DB([a1]), _DB([a2])])
    monkeypatch.setattr(ME, "task_session", lambda: next(dbs))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_enhance, "upscale_image",
                        lambda b, **kw: (b"up", "image/png", 10, 10))
    monkeypatch.setattr(ME.image_enhance, "remove_background",
                        AsyncMock(return_value=(b"bg", "image/png")))
    import app.services.media_storage as ms
    monkeypatch.setattr(ms, "persist_generated_image", deps["persist"])
    await ME._run_batch([str(a1.id)], "upscale", {})
    await ME._run_batch([str(a2.id)], "remove_bg", {})
    assert deps["persist"].await_count == 2


@pytest.mark.asyncio
async def test_run_batch_smart_crop_ai_and_local(deps, monkeypatch):
    a1, a2 = _asset(), _asset()
    dbs = iter([_DB([a1]), _DB([a2])])
    monkeypatch.setattr(ME, "task_session", lambda: next(dbs))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_enhance, "smart_crop_async",
                        AsyncMock(return_value=(b"ai", "image/png", 1, 1)))
    monkeypatch.setattr(ME.image_enhance, "smart_crop",
                        lambda b, **kw: (b"noai", "image/png", 1, 1))
    import app.services.media_storage as ms
    monkeypatch.setattr(ms, "persist_generated_image", deps["persist"])
    await ME._run_batch([str(a1.id)], "smart_crop", {"use_ai": True})
    await ME._run_batch([str(a2.id)], "smart_crop", {"use_ai": False})
    assert deps["persist"].await_count == 2


@pytest.mark.asyncio
async def test_run_batch_alt_text_spellchecked(deps, monkeypatch):
    asset = _asset()
    monkeypatch.setattr(ME, "task_session", lambda: _DB([asset]))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_enhance, "generate_alt_text",
                        AsyncMock(return_value="a raw caption"))
    import app.services.media_spellcheck as msc
    monkeypatch.setattr(msc, "correct_text", AsyncMock(return_value="a fixed caption"))
    db = _DB([asset])
    monkeypatch.setattr(ME, "task_session", lambda: db)
    await ME._run_batch([str(asset.id)], "alt_text", {})
    assert asset.alt_text == "a fixed caption"
    assert db.commits == 1
    deps["persist"].assert_not_awaited()  # alt_text stores on asset, not new asset


@pytest.mark.asyncio
async def test_run_batch_unknown_op_and_failure_resilience(deps, monkeypatch):
    a1, a2 = _asset(), _asset()
    monkeypatch.setattr(ME, "task_session", lambda: _DB([a1, a2]))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(ME.image_transform, "resize_image",
                        lambda b, **kw: SimpleNamespace(image_bytes=b"o", mime_type="image/png"))
    monkeypatch.setattr(ME.image_transform, "convert_format",
                        lambda b, **kw: (_ for _ in ()).throw(RuntimeError("transform fail")))
    import app.services.media_storage as ms
    monkeypatch.setattr(ms, "persist_generated_image", deps["persist"])
    # unknown op: skipped silently; bad transform: logged, loop continues
    await ME._run_batch([str(a1.id), str(a2.id)], "bogus_op", {})
    await ME._run_batch([str(a1.id)], "convert", {})
    deps["sync"].assert_awaited()


def test_task_wrapper(monkeypatch):
    monkeypatch.setattr(ME, "run_async", lambda c: c.close())
    ME.batch_enhance_task(["x"], "resize", {})  # no raise

    def _raise(c):
        c.close()
        raise RuntimeError("run fail")
    monkeypatch.setattr(ME, "run_async", _raise)
    ME.batch_enhance_task(["x"], "resize", {})  # swallowed, logged
