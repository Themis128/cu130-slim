"""Integration tests for the modern media library: collections, search, and tagging."""
import io

import pytest
from PIL import Image

TEST_USER = {"email": "media-lib-test@example.com", "password": "TestPass123!", "name": "Media Lib Test"}


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


@pytest.mark.asyncio
async def test_collection_crud_and_asset_assignment(client):
    headers = await _register_and_login(client)
    png = _make_png()

    # Upload an asset
    upload = await client.post(
        "/api/v1/media/upload",
        data={"alt_text": "test png"},
        files={"file": ("pic.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    # Create a collection
    col = await client.post(
        "/api/v1/media/collections",
        json={"name": "Q4 Photos"},
        headers=headers,
    )
    assert col.status_code == 201, col.text
    collection_id = col.json()["id"]
    assert col.json()["name"] == "Q4 Photos"

    # Add asset to collection
    add = await client.post(
        f"/api/v1/media/collections/{collection_id}/assets",
        json={"asset_id": asset_id},
        headers=headers,
    )
    assert add.status_code == 200, add.text
    assert add.json()["collection_id"] == collection_id

    # List collections and see asset_count=1
    list_resp = await client.get("/api/v1/media/collections", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    collections = list_resp.json()["collections"]
    assert any(c["id"] == collection_id and c["asset_count"] == 1 for c in collections)

    # Remove asset from collection
    remove = await client.delete(f"/api/v1/media/collections/{collection_id}/assets/{asset_id}", headers=headers)
    assert remove.status_code == 204, remove.text

    # Delete collection
    delete = await client.delete(f"/api/v1/media/collections/{collection_id}", headers=headers)
    assert delete.status_code == 204, delete.text


@pytest.mark.asyncio
async def test_search_and_filters(client):
    headers = await _register_and_login(client)
    png = _make_png(color=(10, 100, 200))

    upload = await client.post(
        "/api/v1/media/upload",
        data={"alt_text": "beach sunset"},
        files={"file": ("beach.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    # Search by filename
    search = await client.get("/api/v1/media/search", params={"q": "beach"}, headers=headers)
    assert search.status_code == 200, search.text
    assert any(a["id"] == asset_id for a in search.json()["assets"])

    # Search by alt_text
    search = await client.get("/api/v1/media/search", params={"q": "sunset"}, headers=headers)
    assert search.status_code == 200, search.text
    assert any(a["id"] == asset_id for a in search.json()["assets"])

    # Filter by mime_type
    search = await client.get("/api/v1/media/search", params={"mime_type": "image/png"}, headers=headers)
    assert search.status_code == 200, search.text
    assert any(a["id"] == asset_id for a in search.json()["assets"])

    # Filter by source=upload
    search = await client.get("/api/v1/media/search", params={"source": "upload"}, headers=headers)
    assert search.status_code == 200, search.text
    assert any(a["id"] == asset_id for a in search.json()["assets"])


@pytest.mark.asyncio
async def test_manual_retag_endpoint(client):
    from unittest.mock import patch

    headers = await _register_and_login(client)
    png = _make_png(color=(50, 50, 200))

    upload = await client.post(
        "/api/v1/media/upload",
        data={"alt_text": "coastline"},
        files={"file": ("coast.png", png, "image/png")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    with patch("app.api.media.celery_app.send_task") as mock_send:
        resp = await client.post(f"/api/v1/media/assets/{asset_id}/tag", headers=headers)
    assert resp.status_code == 200, resp.text
    assert mock_send.called
    assert str(asset_id) in mock_send.call_args.kwargs["args"]
