"""Unit tests for MinIO storage and the R2 → MinIO → local fallback chain."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import minio_storage


@pytest.mark.asyncio
async def test_minio_enabled_true():
    with (
        patch.object(minio_storage.settings, "MINIO_ENDPOINT", "minio:9000"),
        patch.object(minio_storage.settings, "MINIO_ACCESS_KEY", "minioadmin"),
        patch.object(minio_storage.settings, "MINIO_SECRET_KEY", "minioadmin"),
    ):
        assert minio_storage.minio_enabled() is True


@pytest.mark.asyncio
async def test_minio_enabled_false_no_credentials():
    with (
        patch.object(minio_storage.settings, "MINIO_ENDPOINT", ""),
        patch.object(minio_storage.settings, "MINIO_ACCESS_KEY", ""),
        patch.object(minio_storage.settings, "MINIO_SECRET_KEY", ""),
    ):
        assert minio_storage.minio_enabled() is False


@pytest.mark.asyncio
async def test_upload_object_success():
    fake_client = MagicMock()
    fake_client.put_object.return_value = {"ETag": '"abc123"'}
    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_ENDPOINT", "minio:9000"),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
        patch.object(minio_storage.settings, "MINIO_SECURE", False),
    ):
        result = await minio_storage.upload_object("test/file.txt", b"hello", "text/plain")
    assert result["key"] == "test/file.txt"
    assert result["etag"] == "abc123"
    assert result["size"] == 5
    assert "minio:9000/social-media/test/file.txt" in result["public_url"]


@pytest.mark.asyncio
async def test_upload_object_no_bucket():
    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        with pytest.raises(Exception, match="MinIO is not configured"):
            await minio_storage.upload_object("test/file.txt", b"hello")


@pytest.mark.asyncio
async def test_get_object_success():
    fake_body = MagicMock()
    fake_body.read.return_value = b"file content"
    fake_client = MagicMock()
    fake_client.get_object.return_value = {"Body": fake_body}
    fake_client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        data = await minio_storage.get_object("test/file.txt")
    assert data == b"file content"


@pytest.mark.asyncio
async def test_get_object_not_found():
    fake_client = MagicMock()
    no_such_key = type("NoSuchKey", (Exception,), {})
    fake_client.exceptions.NoSuchKey = no_such_key
    fake_client.get_object.side_effect = no_such_key()

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        with pytest.raises(Exception, match="not found"):
            await minio_storage.get_object("missing/file.txt")


@pytest.mark.asyncio
async def test_delete_object_success():
    fake_client = MagicMock()
    fake_client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    fake_client.delete_object.return_value = {}

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        result = await minio_storage.delete_object("test/file.txt")
    assert result is True


@pytest.mark.asyncio
async def test_delete_object_not_found_returns_true():
    fake_client = MagicMock()
    no_such_key = type("NoSuchKey", (Exception,), {})
    fake_client.exceptions.NoSuchKey = no_such_key
    fake_client.delete_object.side_effect = no_such_key()

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        result = await minio_storage.delete_object("missing/file.txt")
    assert result is True


@pytest.mark.asyncio
async def test_object_exists_true():
    fake_client = MagicMock()
    fake_client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    fake_client.head_object.return_value = {}

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        result = await minio_storage.object_exists("test/file.txt")
    assert result is True


@pytest.mark.asyncio
async def test_object_exists_false():
    fake_client = MagicMock()
    no_such_key = type("NoSuchKey", (Exception,), {})
    fake_client.exceptions.NoSuchKey = no_such_key
    fake_client.head_object.side_effect = no_such_key()

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        result = await minio_storage.object_exists("missing/file.txt")
    assert result is False
