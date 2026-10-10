"""Endpoint-level tests for app/api/media.py.

Covers the image re-encode helper, quality gate, upload/view/list/CRUD,
generate-image fallback chain, generate-video enqueue, presigned-upload
flow, collections, search, and retag/similar — previously ~33%.
"""

from __future__ import annotations

import io
import sys
import uuid
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image

from app.api import media
from app.api.media import (
    BulkDeleteRequest,
    CollectionAssetRequest,
    CompleteUploadRequest,
    MediaAssetUpdateRequest,
    MediaCollectionCreateRequest,
    MediaCollectionUpdateRequest,
    MediaGenerateImageRequest,
    MediaGenerateOptions,
    MediaGenerateVideoOptions,
    MediaGenerateVideoRequest,
    PresignedUploadRequest,
    _reencode_image_bytes,
    _score_and_store_quality,
    add_asset_to_collection,
    bulk_delete_media,
    complete_upload,
    create_collection,
    delete_collection,
    delete_media,
    generate_image,
    generate_video,
    generate_video_status,
    get_collection,
    get_media,
    list_collections,
    list_media,
    prepare_upload,
    remove_asset_from_collection,
    retag_asset,
    search_media,
    similar_assets,
    update_collection,
    update_media,
    upload_media,
    view_media,
)
from app.core.config import settings

# ── Fakes ─────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalar_one(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


class _DB:
    def __init__(self, results=(), team=None, scalar_value=0):
        self._q = list(results)
        self._team = team
        self._scalar_value = scalar_value
        self.added = []
        self.deleted = []
        self.committed = 0

    async def get(self, model, _id):
        return self._team

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    async def scalar(self, stmt):
        return self._scalar_value

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        now = datetime.now(UTC)
        for attr in ("id", "created_at", "updated_at"):
            if getattr(obj, attr, None) is None:
                setattr(obj, attr, uuid.uuid4() if attr == "id" else now)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def rollback(self):
        pass


def _team(**kw):
    return SimpleNamespace(id=kw.pop("id", uuid.uuid4()), name="T", **kw)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email=settings.SOCIAL_ADMIN_EMAIL)


def _asset(**kw):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        collection_id=kw.pop("collection_id", None),
        filename=kw.pop("filename", "a.png"),
        mime_type=kw.pop("mime_type", "image/png"),
        size_bytes=100,
        storage_backend=kw.pop("storage_backend", "local"),
        storage_path=kw.pop("storage_path", "x/a.png"),
        public_url=None,
        width=100,
        height=100,
        duration_seconds=None,
        alt_text=None,
        tags=[],
        ai_tags=[],
        ai_caption=None,
        source="upload",
        generation_prompt=None,
        is_favorite=False,
        is_archived=False,
        usage_count=0,
        meta_data=kw.pop("meta_data", {}),
        user_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        **kw,
    )


def _collection(**kw):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        user_id=uuid.uuid4(),
        name=kw.pop("name", "Col"),
        description=None,
        cover_asset_id=None,
        created_at=now,
        updated_at=now,
        **kw,
    )


class _Upload:
    def __init__(self, filename, content, content_type="image/png"):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self):
        return self._content


def _png_bytes(w=4, h=4, mode="RGBA"):
    img = Image.new(mode, (w, h), (255, 0, 0, 128) if mode == "RGBA" else 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_orm(monkeypatch):
    monkeypatch.setattr(media, "flag_modified", lambda *a, **k: None)


# ── _reencode_image_bytes ─────────────────────────────────────────────


def test_reencode_png_passes_alpha():
    out, mime = _reencode_image_bytes(_png_bytes(), out_format="PNG")
    assert mime == "image/png"
    assert Image.open(io.BytesIO(out)).mode == "RGBA"


def test_reencode_jpeg_flattens_alpha():
    out, mime = _reencode_image_bytes(_png_bytes(mode="RGBA"), out_format="JPEG")
    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"


def test_reencode_jpeg_rgb_unchanged():
    out, mime = _reencode_image_bytes(_png_bytes(mode="RGB"), out_format="JPEG")
    assert mime == "image/jpeg"


# ── _score_and_store_quality ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_quality_score_stored(monkeypatch):
    mod = ModuleType("app.services.image_enhance")
    mod.score_image_quality = lambda b: SimpleNamespace(
        overall=90.0, sharpness=80.0, brightness=50, contrast=50, blur_detected=False, too_dark=False, too_bright=False, issues=[]
    )
    monkeypatch.setitem(sys.modules, "app.services.image_enhance", mod)
    asset = _asset(meta_data=None)
    db = _DB()
    await _score_and_store_quality(asset, _png_bytes(), db)
    assert asset.meta_data["quality_score"]["overall"] == 90.0
    assert asset.meta_data["quality_failed"] is False
    assert db.committed == 1


@pytest.mark.asyncio
async def test_quality_score_flags_blurry(monkeypatch):
    mod = ModuleType("app.services.image_enhance")
    mod.score_image_quality = lambda b: SimpleNamespace(
        overall=10.0, sharpness=1.0, brightness=50, contrast=50, blur_detected=True, too_dark=False, too_bright=False, issues=["blur"]
    )
    monkeypatch.setitem(sys.modules, "app.services.image_enhance", mod)
    asset = _asset()
    await _score_and_store_quality(asset, b"x", _DB())
    assert asset.meta_data["quality_failed"] is True


@pytest.mark.asyncio
async def test_quality_score_failure_tolerated(monkeypatch):
    mod = ModuleType("app.services.image_enhance")
    mod.score_image_quality = lambda b: (_ for _ in ()).throw(RuntimeError("scorer down"))
    monkeypatch.setitem(sys.modules, "app.services.image_enhance", mod)
    asset = _asset()
    await _score_and_store_quality(asset, b"x", _DB())
    assert asset.meta_data == {}  # untouched


# ── upload_media ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_400_no_team():
    with pytest.raises(HTTPException) as e:
        await upload_media(uuid.uuid4(), _Upload("a.png", b"x"), None, "", _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_400_bad_extension():
    with pytest.raises(HTTPException) as e:
        await upload_media(uuid.uuid4(), _Upload("a.exe", b"x"), None, "", _user(), _DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_image_downscales_and_scores(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _asset(team_id=team.id)
    downscaled = []
    monkeypatch.setattr(media, "downscale_image_bytes", lambda c: downscaled.append(c) or (b"small", 2, 2))
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    scored = []
    monkeypatch.setattr(media, "_score_and_store_quality", AsyncMock(side_effect=lambda *a: scored.append(a)))
    out = await upload_media(team.id, _Upload("a.png", _png_bytes()), "alt", "t1,t2", _user(), db)
    assert out is asset
    assert len(scored) == 1
    media.save_uploaded_media.assert_awaited_once()
    assert media.save_uploaded_media.await_args.kwargs["tags"] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_upload_non_image_skips_quality(monkeypatch):
    team = _team()
    db = _DB(team=team)
    asset = _asset(team_id=team.id, mime_type="video/mp4")
    monkeypatch.setattr(media, "save_uploaded_media", AsyncMock(return_value=asset))
    scored = AsyncMock()
    monkeypatch.setattr(media, "_score_and_store_quality", scored)
    await upload_media(team.id, _Upload("v.mp4", b"vid", "video/mp4"), None, "", _user(), db)
    scored.assert_not_awaited()


# ── view_media ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_view_local_direct_serve(tmp_path, monkeypatch):
    (tmp_path / "pic.png").write_bytes(_png_bytes())
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    out = await view_media(path="pic.png", format=None)
    assert out.media_type == "image/png"


@pytest.mark.asyncio
async def test_view_local_force_jpeg(tmp_path, monkeypatch):
    (tmp_path / "pic.png").write_bytes(_png_bytes(mode="RGB"))
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    out = await view_media(path="pic.png", format="jpeg")
    assert out.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_view_local_415_unconvertible(tmp_path, monkeypatch):
    (tmp_path / "doc.txt").write_bytes(b"hello")  # txt not in ext map → octet-stream → reencode fails
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as e:
        await view_media(path="doc.txt", format=None)
    assert e.value.status_code == 415


@pytest.mark.asyncio
async def test_view_minio_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))  # no local file
    monkeypatch.setattr(media.minio_storage, "minio_enabled", lambda: True)
    monkeypatch.setattr(media.minio_storage, "get_object", AsyncMock(return_value=_png_bytes(mode="RGB")))
    out = await view_media(path="remote/pic.png", format=None)
    assert out.media_type == "image/png"


@pytest.mark.asyncio
async def test_view_minio_404_falls_to_r2(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media.minio_storage, "minio_enabled", lambda: True)
    monkeypatch.setattr(media.minio_storage, "get_object", AsyncMock(side_effect=HTTPException(status_code=404)))
    monkeypatch.setattr(media.r2_storage, "get_object", AsyncMock(return_value=_png_bytes(mode="RGB")))
    out = await view_media(path="remote/pic.png", format=None)
    assert out.media_type == "image/png"


@pytest.mark.asyncio
async def test_view_404_all_backends(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media.minio_storage, "minio_enabled", lambda: False)
    monkeypatch.setattr(media.r2_storage, "get_object", AsyncMock(side_effect=HTTPException(status_code=404)))
    with pytest.raises(HTTPException) as e:
        await view_media(path="missing.png", format=None)
    assert e.value.status_code == 404


# ── list_media ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_media_no_team_empty():
    out = await list_media(uuid.uuid4(), 1, 20, None, None, None, None, None, _user(), _DB())
    assert out.total == 0 and out.assets == []


@pytest.mark.asyncio
async def test_list_media_returns_assets(monkeypatch):
    team = _team()
    a = _asset(team_id=team.id)
    db = _DB(results=[[a]], team=team, scalar_value=1)
    out = await list_media(team.id, 1, 20, "image", "upload", "newest", "a", None, _user(), db)
    assert out.total == 1
    assert out.assets[0].id == a.id


@pytest.mark.asyncio
async def test_list_media_generated_and_search_filters():
    team = _team()
    db = _DB(results=[[]], team=team, scalar_value=0)
    out = await list_media(team.id, 2, 10, "generated", None, "name_asc", "q", uuid.uuid4(), _user(), db)
    assert out.page == 2


# ── get/update/delete media ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_media_404():
    with pytest.raises(HTTPException) as e:
        await get_media(uuid.uuid4(), uuid.uuid4(), _user(), _DB([None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_update_media_fields(monkeypatch):
    a = _asset()
    monkeypatch.setattr(media, "correct_text", AsyncMock(side_effect=lambda t: f"fixed-{t}"))
    monkeypatch.setattr(media, "correct_tags", AsyncMock(return_value=["tagged"]))
    col = uuid.uuid4()
    db = _DB([a])
    await update_media(
        a.id,
        MediaAssetUpdateRequest(filename="new.png", alt_text="alt", tags=["x"], collection_id=col, is_favorite=True, is_archived=True),
        a.team_id,
        _user(),
        db,
    )
    assert a.filename == "fixed-new.png"
    assert a.alt_text == "fixed-alt"
    assert a.tags == ["tagged"]
    assert a.collection_id == col
    assert a.is_favorite and a.is_archived


@pytest.mark.asyncio
async def test_delete_media_local(tmp_path, monkeypatch):
    f = tmp_path / "gone.png"
    f.write_bytes(b"x")
    a = _asset(storage_backend="local", storage_path="gone.png")
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    db = _DB([a])
    await delete_media(a.id, a.team_id, _user(), db)
    assert not f.exists()
    assert db.deleted == [a]


@pytest.mark.asyncio
async def test_delete_media_r2(monkeypatch):
    a = _asset(storage_backend="r2", storage_path="k/a.png")
    monkeypatch.setattr(media.r2_storage, "delete_object", AsyncMock())
    db = _DB([a])
    await delete_media(a.id, a.team_id, _user(), db)
    media.r2_storage.delete_object.assert_awaited_once_with("k/a.png")


@pytest.mark.asyncio
async def test_delete_media_minio_and_error_tolerated(monkeypatch):
    a = _asset(storage_backend="minio", storage_path="k/m.png")
    monkeypatch.setattr(media.minio_storage, "delete_object", AsyncMock())
    db = _DB([a])
    await delete_media(a.id, a.team_id, _user(), db)
    media.minio_storage.delete_object.assert_awaited_once()

    # local unlink error tolerated
    a2 = _asset(storage_backend="local", storage_path="../escape.png")
    db2 = _DB([a2])
    await delete_media(a2.id, a2.team_id, _user(), db2)
    assert db2.deleted == [a2]


@pytest.mark.asyncio
async def test_bulk_delete(monkeypatch):
    team = _team()
    a1 = _asset(team_id=team.id, storage_backend="r2")
    a2 = _asset(team_id=team.id, storage_backend="minio")
    monkeypatch.setattr(media.r2_storage, "delete_object", AsyncMock())
    monkeypatch.setattr(media.minio_storage, "delete_object", AsyncMock())
    db = _DB(results=[[a1, a2]], team=team)
    out = await bulk_delete_media(BulkDeleteRequest(ids=[a1.id, a2.id]), team.id, _user(), db)
    assert out["deleted"] == 2


@pytest.mark.asyncio
async def test_bulk_delete_400_no_team():
    with pytest.raises(HTTPException) as e:
        await bulk_delete_media(BulkDeleteRequest(ids=[]), uuid.uuid4(), _user(), _DB())
    assert e.value.status_code == 400


# ── generate_image fallback chain ─────────────────────────────────────


def _gen_patches(monkeypatch, *, comfy_png=None, cf_result=None, qa=None, expand=None, asset=None):
    """Wire the local imports used inside generate_image."""
    cf_call = AsyncMock(return_value=cf_result) if cf_result is not None else AsyncMock(side_effect=HTTPException(status_code=502, detail="cf down"))
    monkeypatch.setattr("app.services.inference._call_workers_ai_image", cf_call)

    mod_comfy = ModuleType("app.services.comfyui_image")
    if isinstance(comfy_png, Exception):
        mod_comfy.generate_image = AsyncMock(side_effect=comfy_png)
    else:
        mod_comfy.generate_image = AsyncMock(return_value=(comfy_png, {"model": "flux1-schnell"}))
    monkeypatch.setitem(sys.modules, "app.services.comfyui_image", mod_comfy)

    mod_gpu = ModuleType("app.services.gpu_arbiter")

    class _Lock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return None

    mod_gpu.media_gpu_lock = lambda: _Lock()
    monkeypatch.setitem(sys.modules, "app.services.gpu_arbiter", mod_gpu)

    monkeypatch.setattr("app.services.media_ai.expand_visual_prompt", AsyncMock(side_effect=lambda p, **k: expand or p))
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", AsyncMock(return_value=qa or {"match": True}))

    mod_igfx = ModuleType("app.services.infographic_renderer")
    mod_igfx.is_infographic_request = lambda p: False
    mod_igfx.generate_infographic_content = AsyncMock()
    mod_igfx.render_infographic = lambda c, b, **k: b
    mod_igfx.sanitize_prompt_for_background = lambda p: p
    monkeypatch.setitem(sys.modules, "app.services.infographic_renderer", mod_igfx)

    monkeypatch.setattr(media, "apply_media_quality", AsyncMock(return_value=SimpleNamespace(prompt=None, negative_prompt=None)))
    monkeypatch.setattr(media, "persist_media_quality_metadata", AsyncMock())
    monkeypatch.setattr(media, "_score_and_store_quality", AsyncMock())
    monkeypatch.setattr(media, "persist_generated_image", AsyncMock(return_value=asset or _asset(meta_data={})))
    return cf_call


@pytest.mark.asyncio
async def test_generate_image_400_no_team():
    with pytest.raises(HTTPException) as e:
        await generate_image(MediaGenerateImageRequest(prompt="x"), uuid.uuid4(), _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_image_all_backends_fail_502(monkeypatch):
    team = _team()
    _gen_patches(monkeypatch, comfy_png=RuntimeError("gpu down"), cf_result=None)
    with pytest.raises(HTTPException) as e:
        await generate_image(MediaGenerateImageRequest(prompt="x"), team.id, _user(), _DB(team=team))
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_image_comfy_success(monkeypatch):
    team = _team()
    png = _png_bytes(mode="RGB")
    asset = _asset(team_id=team.id, meta_data={})
    _gen_patches(monkeypatch, comfy_png=png, asset=asset)
    out = await generate_image(MediaGenerateImageRequest(prompt="a cat", options=MediaGenerateOptions(steps=20)), team.id, _user(), _DB(team=team))
    assert out is asset
    assert asset.meta_data["inference_provider"] == "comfyui-flux"
    assert asset.meta_data["semantic_qa"]["match"] is True


@pytest.mark.asyncio
async def test_generate_image_cf_fallback(monkeypatch):
    team = _team()
    png = _png_bytes(mode="RGB")
    import base64

    cf_result = {"image_base64": base64.b64encode(png).decode(), "model": "flux", "provider": "cloudflare"}
    cf_call = _gen_patches(monkeypatch, comfy_png=RuntimeError("gpu down"), cf_result=cf_result)
    out = await generate_image(MediaGenerateImageRequest(prompt="x"), team.id, _user(), _DB(team=team))
    cf_call.assert_awaited_once()
    assert out is not None


@pytest.mark.asyncio
async def test_generate_image_empty_payload_502(monkeypatch):
    team = _team()
    _gen_patches(monkeypatch, comfy_png=RuntimeError("x"), cf_result={"image_base64": "", "model": "m", "provider": "cloudflare"})
    with pytest.raises(HTTPException) as e:
        await generate_image(MediaGenerateImageRequest(prompt="x"), team.id, _user(), _DB(team=team))
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_image_qa_mismatch_retries(monkeypatch):
    team = _team()
    png = _png_bytes(mode="RGB")
    calls = {"n": 0}

    async def _qa(img, prompt):
        calls["n"] += 1
        return {"match": calls["n"] > 1, "reason": "wrong subject"}

    mod = ModuleType("app.services.comfyui_image")
    mod.generate_image = AsyncMock(return_value=(png, {"model": "flux1-schnell"}))
    _gen_patches(monkeypatch, comfy_png=png, qa=None)
    monkeypatch.setitem(sys.modules, "app.services.comfyui_image", mod)
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", AsyncMock(side_effect=_qa))
    asset = _asset(team_id=team.id, meta_data={})
    monkeypatch.setattr(media, "persist_generated_image", AsyncMock(return_value=asset))
    await generate_image(MediaGenerateImageRequest(prompt="x"), team.id, _user(), _DB(team=team))
    assert calls["n"] == 2  # initial + one retry
    assert mod.generate_image.await_count == 2


# ── generate_video ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_video_400_no_team():
    with pytest.raises(HTTPException) as e:
        await generate_video(MediaGenerateVideoRequest(prompt="x"), uuid.uuid4(), _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_400_empty_prompt():
    with pytest.raises(HTTPException) as e:
        await generate_video(MediaGenerateVideoRequest(prompt="  "), uuid.uuid4(), _user(), _DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_400_wrong_model():
    with pytest.raises(HTTPException) as e:
        await generate_video(MediaGenerateVideoRequest(prompt="x", options=MediaGenerateVideoOptions(model="ltxv")), uuid.uuid4(), _user(), _DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_400_bad_builder(monkeypatch):
    mod = ModuleType("app.services.comfyui_video")
    mod.build_wan22_prompt = lambda **k: (_ for _ in ()).throw(ValueError("bad graph"))
    monkeypatch.setitem(sys.modules, "app.services.comfyui_video", mod)
    with pytest.raises(HTTPException) as e:
        await generate_video(MediaGenerateVideoRequest(prompt="x"), uuid.uuid4(), _user(), _DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_400_duration_cap(monkeypatch):
    mod = ModuleType("app.services.comfyui_video")
    mod.build_wan22_prompt = lambda **k: {"graph": True}
    monkeypatch.setitem(sys.modules, "app.services.comfyui_video", mod)
    with pytest.raises(HTTPException) as e:
        await generate_video(
            MediaGenerateVideoRequest(prompt="x", options=MediaGenerateVideoOptions(duration_seconds=61)), uuid.uuid4(), _user(), _DB(team=_team())
        )
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_400_scene_cap(monkeypatch):
    mod = ModuleType("app.services.comfyui_video")
    mod.build_wan22_prompt = lambda **k: {"graph": True}
    monkeypatch.setitem(sys.modules, "app.services.comfyui_video", mod)
    with pytest.raises(HTTPException) as e:
        await generate_video(
            MediaGenerateVideoRequest(prompt="x", options=MediaGenerateVideoOptions(scene_prompts=["a"] * 41)), uuid.uuid4(), _user(), _DB(team=_team())
        )
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_enqueues(monkeypatch):
    mod = ModuleType("app.services.comfyui_video")
    mod.build_wan22_prompt = lambda **k: {"graph": True}
    monkeypatch.setitem(sys.modules, "app.services.comfyui_video", mod)
    sent = []
    monkeypatch.setattr(media.celery_app, "send_task", lambda name, args: sent.append((name, args)) or SimpleNamespace(id="task-1"))
    team = _team()
    out = await generate_video(
        MediaGenerateVideoRequest(prompt="x", options=MediaGenerateVideoOptions(num_frames=82, frame_rate=25)), team.id, _user(), _DB(team=team)
    )
    assert out.task_id == "task-1"
    opts = sent[0][1][3]
    assert opts["num_frames"] == 81  # normalized to 4n+1
    assert opts["frame_rate"] == 24  # normalized


@pytest.mark.asyncio
async def test_generate_video_status(monkeypatch):
    res = SimpleNamespace(status="SUCCESS", successful=lambda: True, failed=lambda: False, result=str(uuid.uuid4()))
    monkeypatch.setattr("celery.result.AsyncResult", lambda *a, **k: res)
    out = await generate_video_status("t1", uuid.uuid4(), _user())
    assert out.status == "SUCCESS"
    assert out.asset_id == res.result


# ── presigned upload ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_upload_r2(monkeypatch):
    url = SimpleNamespace(key="k", upload_url="https://up", public_url="https://pub")
    monkeypatch.setattr(media.r2_presigned, "presigned_upload_url", lambda **k: url)
    out = await prepare_upload(PresignedUploadRequest(filename="f.png", mime_type="image/png", size_bytes=10), uuid.uuid4(), _user(), _DB(team=_team()))
    assert out is url


@pytest.mark.asyncio
async def test_prepare_upload_minio_fallback(monkeypatch):
    url = SimpleNamespace(key="k2", upload_url="https://minio", public_url=None)
    monkeypatch.setattr(media.r2_presigned, "presigned_upload_url", lambda **k: None)
    monkeypatch.setattr(media.minio_storage, "presigned_upload_url", lambda **k: url)
    out = await prepare_upload(PresignedUploadRequest(filename="f.png", mime_type="image/png", size_bytes=10), uuid.uuid4(), _user(), _DB(team=_team()))
    assert out is url


@pytest.mark.asyncio
async def test_prepare_upload_503_no_backend(monkeypatch):
    monkeypatch.setattr(media.r2_presigned, "presigned_upload_url", lambda **k: None)
    monkeypatch.setattr(media.minio_storage, "presigned_upload_url", lambda **k: None)
    with pytest.raises(HTTPException) as e:
        await prepare_upload(PresignedUploadRequest(filename="f.png", mime_type="image/png", size_bytes=10), uuid.uuid4(), _user(), _DB(team=_team()))
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_upload_403_foreign_key(monkeypatch):
    team = _team()
    with pytest.raises(HTTPException) as e:
        await complete_upload(
            CompleteUploadRequest(key="other-team/x.png", filename="x.png", mime_type="image/png", size_bytes=1), team.id, _user(), _DB(team=team)
        )
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_complete_upload_success(monkeypatch):
    team = _team()
    monkeypatch.setattr(media, "correct_text", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(media, "correct_tags", AsyncMock(return_value=["t"]))
    monkeypatch.setattr(media.r2_presigned, "_public_url", lambda k: f"https://pub/{k}")
    monkeypatch.setattr(media.celery_app, "send_task", lambda *a, **k: SimpleNamespace(id="t"))
    db = _DB(team=team)
    out = await complete_upload(
        CompleteUploadRequest(key=f"{team.id}/2026/x.png", filename="x.png", mime_type="image/png", size_bytes=1, alt_text="a"), team.id, _user(), db
    )
    assert out.storage_backend == "r2"
    assert db.committed == 1


# ── collections ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_collection(monkeypatch):
    monkeypatch.setattr(media, "correct_text", AsyncMock(side_effect=lambda t: f"C-{t}"))
    team = _team()
    db = _DB(team=team)
    out = await create_collection(MediaCollectionCreateRequest(name="MyCol"), team.id, _user(), db)
    assert out.asset_count == 0
    assert db.added[0].name == "C-MyCol"


@pytest.mark.asyncio
async def test_list_collections_counts():
    team = _team()
    c = _collection(team_id=team.id)
    db = _DB(results=[[c], [_asset(), _asset()]], team=team)
    out = await list_collections(team.id, _user(), db)
    assert out.total == 1
    assert out.collections[0].asset_count == 2


@pytest.mark.asyncio
async def test_get_collection_404():
    with pytest.raises(HTTPException) as e:
        await get_collection(uuid.uuid4(), uuid.uuid4(), _user(), _DB([None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_update_collection(monkeypatch):
    monkeypatch.setattr(media, "correct_text", AsyncMock(side_effect=lambda t: t))
    c = _collection()
    db = _DB([c, []])
    out = await update_collection(c.id, MediaCollectionUpdateRequest(name="N", description="d", cover_asset_id=uuid.uuid4()), c.team_id, _user(), db)
    assert out.name == "N"
    assert c.description == "d"


@pytest.mark.asyncio
async def test_delete_collection():
    c = _collection()
    db = _DB([c])
    await delete_collection(c.id, c.team_id, _user(), db)
    assert db.deleted == [c]


@pytest.mark.asyncio
async def test_add_asset_to_collection_404():
    with pytest.raises(HTTPException) as e:
        await add_asset_to_collection(uuid.uuid4(), CollectionAssetRequest(asset_id=uuid.uuid4()), uuid.uuid4(), _user(), _DB([None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_add_asset_to_collection_ok():
    c = _collection()
    a = _asset(team_id=c.team_id)
    db = _DB([(c, a)])
    await add_asset_to_collection(c.id, CollectionAssetRequest(asset_id=a.id), c.team_id, _user(), db)
    assert a.collection_id == c.id


@pytest.mark.asyncio
async def test_remove_asset_from_collection():
    c = _collection()
    a = _asset(collection_id=c.id)
    db = _DB([a])
    await remove_asset_from_collection(c.id, a.id, c.team_id, _user(), db)
    assert a.collection_id is None


@pytest.mark.asyncio
async def test_remove_asset_404():
    with pytest.raises(HTTPException) as e:
        await remove_asset_from_collection(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), _user(), _DB([None]))
    assert e.value.status_code == 404


# ── search / retag / similar ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_media_400_no_team():
    with pytest.raises(HTTPException) as e:
        await search_media(uuid.uuid4(), None, None, None, None, None, None, None, None, 1, 20, _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_search_media_filters():
    team = _team()
    a = _asset(team_id=team.id)
    db = _DB(results=[[a.id], [a]], team=team)
    out = await search_media(team.id, "q", "image/png", "upload", uuid.uuid4(), ["t"], True, False, "name", 1, 10, _user(), db)
    assert out.total == 1
    assert out.assets[0].id == a.id


@pytest.mark.asyncio
async def test_retag_asset_404():
    with pytest.raises(HTTPException) as e:
        await retag_asset(uuid.uuid4(), uuid.uuid4(), _user(), _DB(results=[None], team=_team()))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_retag_asset_enqueues(monkeypatch):
    team = _team()
    a = _asset(team_id=team.id)
    sent = []
    monkeypatch.setattr(media.celery_app, "send_task", lambda *a_, **k: sent.append(a_))
    db = _DB(results=[a], team=team)
    await retag_asset(a.id, team.id, _user(), db)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_similar_assets_empty(monkeypatch):
    team = _team()
    a = _asset(team_id=team.id)
    monkeypatch.setattr(media, "get_similar_assets", AsyncMock(return_value=[]))
    out = await similar_assets(a.id, team.id, _user(), _DB(results=[a], team=team))
    assert out == []


@pytest.mark.asyncio
async def test_similar_assets_skips_bad_ids(monkeypatch):
    team = _team()
    a = _asset(team_id=team.id)
    other = _asset(team_id=team.id)
    monkeypatch.setattr(
        media,
        "get_similar_assets",
        AsyncMock(
            return_value=[
                {"embedding_id": str(other.id)},
                {"embedding_id": "not-a-uuid"},
                {"embedding_id": None},
            ]
        ),
    )
    db = _DB(results=[a, [(other.id, "cap")]], team=team)
    out = await similar_assets(a.id, team.id, _user(), db)
    assert len(out) == 1
    assert out[0].ai_caption == "cap"
