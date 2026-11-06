"""Coverage for media.py tails: probe-none arm, MinIO->R2 fallback,
sweep_orphans, and the generate-image text-QA retry block."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import media
from app.api.media import generate_image, sweep_orphans, upload_media, view_media
from app.services import video_ai
from tests.unit.test_media_api_router import (
    _DB,
    _asset,
    _png_bytes,
    _team,
    _Upload,
    _user,
)

pytestmark = pytest.mark.asyncio


# ── upload video probe: falsy probe → asset returned untouched ──────────


async def test_probe_none_skips(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _asset(team_id=team.id, mime_type="video/mp4", filename="v.mp4")
    asset.meta_data = {"a": 1}
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    monkeypatch.setattr(video_ai, "probe_video", AsyncMock(return_value=None))

    out = await upload_media(team.id, _Upload("v.mp4", b"vid", "video/mp4"), None, "", _user(), db)
    assert out is asset and asset.meta_data == {"a": 1}


# ── view_media: MinIO generic exception -> R2 fallback ──────────────────


async def test_view_minio_exception_falls_to_r2(monkeypatch):
    from app.services import minio_storage, r2_storage

    monkeypatch.setattr(minio_storage, "minio_enabled", lambda: True)
    monkeypatch.setattr(minio_storage, "get_object", AsyncMock(side_effect=Exception("boom")))
    monkeypatch.setattr(r2_storage, "get_object", AsyncMock(return_value=_png_bytes()))
    monkeypatch.setattr(media, "safe_resolve", lambda d, p: SimpleNamespace(is_file=lambda: False))
    out = await view_media(path="p/x.png", format=None)
    assert out.status_code == 200


# ── sweep_orphans ────────────────────────────────────────────────────────


async def test_sweep_orphans_all_backends(monkeypatch):
    from app.services import minio_storage, r2_storage

    team = _team()
    r2_hit = _asset(team_id=team.id, storage_backend="r2", storage_path="a.png")
    minio_hit = _asset(team_id=team.id, storage_backend="minio", storage_path="b.png")
    local_miss = _asset(team_id=team.id, storage_backend="local", storage_path="gone.png")
    weird = _asset(team_id=team.id, storage_backend="nope", storage_path="w.png")
    db = _DB(results=[[r2_hit, minio_hit, local_miss, weird], [r2_hit, minio_hit, local_miss, weird]], team=team)
    db.deleted = []
    db.delete = AsyncMock(side_effect=lambda a: db.deleted.append(a))
    monkeypatch.setattr(r2_storage, "object_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(minio_storage, "object_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "safe_resolve", lambda d, p: SimpleNamespace(is_file=lambda: False))

    out = await sweep_orphans(team.id, _user(), db, dry_run=False)
    assert out["kept"] == 2 and out["orphans"] == 2 and len(db.deleted) == 2
    db.deleted.clear()
    out = await sweep_orphans(team.id, _user(), db, dry_run=True)
    assert out["orphans"] == 2 and not db.deleted
    db2 = _DB(team=None)
    with pytest.raises(Exception) as ei:
        await sweep_orphans(team.id, _user(), db2)
    assert "400" in str(ei.value)


# ── generate_image: text-QA retry block (798-830) ────────────────────────


async def test_generate_text_qa_retry_workers_ai(monkeypatch):
    import app.services.comfyui_image as CI
    import app.services.image_text_guard as ITG
    import app.services.inference as INF
    import app.services.media_ai as MA
    from app.services import infographic_renderer as IR

    team = _team()
    db = _DB(team=team)
    db.get = AsyncMock(return_value=team)
    asset = _asset(team_id=team.id)
    asset.meta_data = {}

    monkeypatch.setattr(media, "apply_media_quality", AsyncMock(return_value=SimpleNamespace(prompt="p", negative_prompt=None)))
    monkeypatch.setattr(MA, "expand_visual_prompt", AsyncMock(return_value="expanded"))
    monkeypatch.setattr(MA, "verify_media_semantics", AsyncMock(return_value={"match": True, "reason": ""}))
    monkeypatch.setattr(ITG, "wants_rendered_text", lambda p: False)
    monkeypatch.setattr(IR, "is_infographic_request", lambda p: False)
    monkeypatch.setattr(ITG, "check_image_text", AsyncMock(return_value={"garbled": True, "checked": True}))
    monkeypatch.setattr(IR, "sanitize_prompt_for_background", lambda p: "sanitized " + p)

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "media_gpu_lock", lambda: _Lock())
    monkeypatch.setattr(CI, "generate_image", AsyncMock(side_effect=Exception("gpu busy")))

    gen = {"image_base64": base64.b64encode(b"png").decode(), "model": "flux", "provider": "cloudflare"}
    monkeypatch.setattr(INF, "_call_workers_ai_image", AsyncMock(return_value=gen))
    monkeypatch.setattr(media, "persist_generated_image", AsyncMock(return_value=asset))
    monkeypatch.setattr(media, "flag_modified", lambda *a: None)
    monkeypatch.setattr(media, "_score_and_store_quality", AsyncMock())
    monkeypatch.setattr(media, "persist_media_quality_metadata", AsyncMock())

    body = SimpleNamespace(prompt="a sunset", options=None)
    out = await generate_image(body, team.id, _user(), db)
    assert out is asset
    assert INF._call_workers_ai_image.await_count == 2
    assert ITG.check_image_text.await_count == 2
    assert asset.meta_data["text_qa"]["garbled"] is True


async def test_generate_text_qa_retry_comfyui(monkeypatch):
    import app.services.comfyui_image as CI
    import app.services.gpu_arbiter as GA
    import app.services.image_text_guard as ITG
    import app.services.media_ai as MA
    from app.services import infographic_renderer as IR

    team = _team()
    db = _DB(team=team)
    db.get = AsyncMock(return_value=team)
    asset = _asset(team_id=team.id)
    asset.meta_data = {}

    monkeypatch.setattr(media, "apply_media_quality", AsyncMock(return_value=SimpleNamespace(prompt="p", negative_prompt=None)))
    monkeypatch.setattr(MA, "expand_visual_prompt", AsyncMock(return_value="expanded"))
    monkeypatch.setattr(MA, "verify_media_semantics", AsyncMock(return_value={"match": True, "reason": ""}))
    monkeypatch.setattr(ITG, "wants_rendered_text", lambda p: False)
    monkeypatch.setattr(IR, "is_infographic_request", lambda p: False)
    monkeypatch.setattr(ITG, "check_image_text", AsyncMock(return_value={"garbled": True, "checked": True}))
    monkeypatch.setattr(IR, "sanitize_prompt_for_background", lambda p: "s " + p)

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(GA, "media_gpu_lock", lambda: _Lock())
    monkeypatch.setattr(CI, "generate_image", AsyncMock(return_value=(b"png", {"model": "flux1-schnell"})))
    monkeypatch.setattr(media, "persist_generated_image", AsyncMock(return_value=asset))
    monkeypatch.setattr(media, "flag_modified", lambda *a: None)
    monkeypatch.setattr(media, "_score_and_store_quality", AsyncMock())
    monkeypatch.setattr(media, "persist_media_quality_metadata", AsyncMock())

    body = SimpleNamespace(prompt="a sunset", options=None)
    out = await generate_image(body, team.id, _user(), db)
    assert out is asset and CI.generate_image.await_count == 2
