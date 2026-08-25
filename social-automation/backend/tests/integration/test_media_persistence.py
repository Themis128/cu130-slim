"""End-to-end tests: AI-generated content must be stored in the DB (media_assets)
and be retrievable via the Media Library endpoints."""
import io

import pytest
from PIL import Image
from unittest.mock import AsyncMock, patch

TEST_USER = {"email": "media-persist-test@example.com", "password": "TestPass123!", "name": "Media Persist Test"}


def _make_png(color=(200, 30, 30), size=(64, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def _register_and_login(client) -> dict:
    await client.post("/api/v1/auth/register", json=TEST_USER)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _assert_asset_in_media_library(client, headers, gen_data, prompt):
    """Shared assertions: asset is in DB listing and servable via /media/view."""

    # 1. Asset shows up in the Media Library listing
    listing = await client.get("/api/v1/media/assets", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] >= 1
    assets = [a for a in body["assets"] if a["id"] == gen_data["asset_id"]]
    assert len(assets) == 1, f"generated asset {gen_data['asset_id']} not in media library"
    asset = assets[0]
    assert asset["source"] == "ai-generated"
    assert asset["generation_prompt"] == prompt
    assert asset["alt_text"] == prompt
    assert asset["mime_type"] == "image/png"
    assert asset["width"] == 64 and asset["height"] == 32

    # 2. File was written to disk under the upload dir and is servable
    view = await client.get("/api/v1/media/view", params={"path": asset["storage_path"]})
    assert view.status_code == 200, view.text
    assert view.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(view.content))
    assert img.size == (64, 32)


@pytest.mark.asyncio
async def test_generate_image_persists_to_media_library(client):
    """POST /ai/generate-image must create a media_assets row + disk file."""
    headers = await _register_and_login(client)
    png = _make_png()

    with (
        patch("app.api.ai._call_nvidia_flux_dev", new=AsyncMock(return_value=png)),
        patch(
            "app.services.inference._get_provider_config",
            new=AsyncMock(return_value=("http://fake-url", "fake-model", "fake-key")),
        ),
        patch("app.api.ai.chroma_client.query_similar", new=AsyncMock(return_value=[])),
        patch("app.api.ai.chroma_client.add_content", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/ai/generate-image",
            json={"prompt": "a test sunset over mountains"},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["image_base64"], "base64 payload still returned for backwards compat"
    assert data["asset_id"], "asset_id missing — image was not persisted"
    assert data["storage_path"], "storage_path missing — image was not persisted"

    await _assert_asset_in_media_library(client, headers, data, "a test sunset over mountains")


@pytest.mark.asyncio
async def test_generate_image_pipeline_persists_to_media_library(client):
    """POST /ai/generate-image-pipeline must persist the final enhanced image."""
    headers = await _register_and_login(client)
    png = _make_png()

    with (
        patch("app.services.inference._get_provider_config", new=AsyncMock(return_value=("http://fake-url", "m", "k"))),
        patch("app.api.ai._call_nvidia_flux_pipeline", new=AsyncMock(return_value=png)),
        patch("app.api.ai.chroma_client.query_similar", new=AsyncMock(return_value=[])),
        patch("app.api.ai.chroma_client.add_content", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/ai/generate-image-pipeline",
            json={"prompt": "pipeline test image"},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["asset_id"], "asset_id missing from pipeline response"

    await _assert_asset_in_media_library(client, headers, data, "pipeline test image")


@pytest.mark.asyncio
async def test_generate_image_flux_persists_to_media_library(client):
    """POST /ai/generate-image-flux must persist the generated image."""
    headers = await _register_and_login(client)
    png = _make_png()

    with (
        patch("app.services.inference._get_provider_config", new=AsyncMock(return_value=("http://fake-url", "m", "k"))),
        patch("app.api.ai._call_nvidia_flux", new=AsyncMock(return_value=png)),
        patch("app.api.ai.chroma_client.add_content", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/ai/generate-image-flux",
            json={"prompt": "flux test image"},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["asset_id"], "asset_id missing from flux response"

    await _assert_asset_in_media_library(client, headers, data, "flux test image")


@pytest.mark.asyncio
async def test_generated_assets_are_paged_with_uploads(client):
    """Generated assets coexist with uploads in the Media Library pagination."""
    headers = await _register_and_login(client)
    png = _make_png(color=(10, 10, 200))

    with (
        patch("app.api.ai._call_nvidia_flux_dev", new=AsyncMock(return_value=png)),
        patch("app.services.inference._get_provider_config", new=AsyncMock(return_value=("u", "m", "k"))),
        patch("app.api.ai.chroma_client.query_similar", new=AsyncMock(return_value=[])),
        patch("app.api.ai.chroma_client.add_content", new=AsyncMock()),
    ):
        gen = await client.post("/api/v1/ai/generate-image", json={"prompt": "coexist test"}, headers=headers)
    assert gen.status_code == 200, gen.text

    upload = await client.post(
        "/api/v1/media/upload",
        data={"alt_text": "uploaded png"},
        files={"file": ("direct-upload.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text

    listing = await client.get("/api/v1/media/assets", headers=headers)
    body = listing.json()
    ids = {a["id"] for a in body["assets"]}
    sources = {a["id"]: a["source"] for a in body["assets"]}
    assert gen.json()["asset_id"] in ids
    assert str(upload.json()["id"]) in ids
    assert sources[gen.json()["asset_id"]] == "ai-generated"
    assert sources[str(upload.json()["id"])] == "upload"

@pytest.mark.asyncio
async def test_comfyui_job_completion_persists_to_media_library(client):
    """GET /ai/generate-image/{job_id} must persist the ComfyUI output once."""
    import httpx as httpx_mod
    from app.api import ai as ai_module

    headers = await _register_and_login(client)
    job_id = "test-job-123"
    comfy_png = _make_png(color=(0, 180, 0))

    history_payload = {
        job_id: {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}
                    ]
                }
            }
        }
    }

    class FakeResponse:
        def __init__(self, status_code, json_data=None, content=b""):
            self.status_code = status_code
            self._json = json_data or {}
            self.content = content

        def json(self):
            return self._json

    real_async_client = httpx_mod.AsyncClient

    class FakeAsyncClient(real_async_client):
        async def get(self, url, **kwargs):
            if "/history/" in url:
                return FakeResponse(200, history_payload)
            if url.endswith("/view"):
                return FakeResponse(200, content=comfy_png)
            return await super().get(url, **kwargs)

    with (
        patch.object(ai_module.httpx, "AsyncClient", FakeAsyncClient),
        patch.object(ai_module.settings, "COMFYUI_URL", "http://fake-comfyui"),
    ):
        first = await client.get(f"/api/v1/ai/generate-image/{job_id}", headers=headers)
        second = await client.get(f"/api/v1/ai/generate-image/{job_id}", headers=headers)

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "completed"
    assert body["asset_id"], "comfyui output not persisted on completion"

    # Second poll of the same job must NOT duplicate the asset
    assert second.status_code == 200
    assert second.json()["asset_id"] is None, "duplicate asset created for same job"

    listing = await client.get("/api/v1/media/assets", headers=headers)
    assets = listing.json()["assets"]
    matches = [a for a in assets if a["source"] == "comfyui"]
    assert len(matches) == 1
    assert matches[0]["filename"] == "ComfyUI_00001_.png"


@pytest.mark.asyncio
async def test_media_type_filters(client, db):
    """GET /media/assets?type=… must filter images / videos / AI generated."""
    import uuid

    from sqlalchemy import select as sa_select

    from app.models.content import MediaAsset
    from app.models.user import Team

    headers = await _register_and_login(client)
    png = _make_png(color=(90, 90, 200))

    # 1. An uploaded image
    upload = await client.post(
        "/api/v1/media/upload",
        files={"file": ("pic.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text

    # 2. An AI generated image
    with (
        patch("app.api.ai._call_nvidia_flux_dev", new=AsyncMock(return_value=png)),
        patch("app.services.inference._get_provider_config", new=AsyncMock(return_value=("u", "m", "k"))),
        patch("app.api.ai.chroma_client.query_similar", new=AsyncMock(return_value=[])),
        patch("app.api.ai.chroma_client.add_content", new=AsyncMock()),
    ):
        gen = await client.post("/api/v1/ai/generate-image", json={"prompt": "filter test"}, headers=headers)
    assert gen.status_code == 200, gen.text

    # 3. A video asset (inserted directly; upload endpoint targets stills)
    me = await client.get("/api/v1/auth/me", headers=headers)
    user_id = uuid.UUID(me.json()["id"])
    team = (await db.execute(sa_select(Team).where(Team.owner_id == user_id))).scalars().first()
    assert team is not None
    vid = MediaAsset(
        team_id=team.id,
        user_id=user_id,
        filename="clip.mp4",
        mime_type="video/mp4",
        storage_path="2026/01/01/clip.mp4",
        source="upload",
    )
    db.add(vid)
    await db.commit()

    async def _ids(params):
        r = await client.get("/api/v1/media/assets", params=params, headers=headers)
        assert r.status_code == 200, r.text
        return {a["id"] for a in r.json()["assets"]}

    all_ids = await _ids({})
    assert len(all_ids) == 3

    image_ids = await _ids({"type": "image"})
    assert image_ids == {str(upload.json()["id"]), gen.json()["asset_id"]}

    video_ids = await _ids({"type": "video"})
    assert video_ids == {str(vid.id)}

    generated_ids = await _ids({"type": "generated"})
    assert generated_ids == {gen.json()["asset_id"]}

    # Exact-source filter still works alongside
    upload_only = await _ids({"source": "upload"})
    assert upload_only == {str(upload.json()["id"]), str(vid.id)}


