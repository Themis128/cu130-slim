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
    fake_client.put_object.return_value = "abc123"
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
    assert "/api/v1/media/view?path=test/file.txt" in result["public_url"]


@pytest.mark.asyncio
async def test_upload_object_no_bucket():
    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        with pytest.raises(Exception, match="MinIO is not configured"):
            await minio_storage.upload_object("test/file.txt", b"hello")


@pytest.mark.asyncio
async def test_get_object_success():
    fake_client = MagicMock()
    fake_client.get_object.return_value = b"file content"

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
    fake_client.get_object.side_effect = minio_storage.S3Error(404)

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
    fake_client.delete_object.return_value = None

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
    fake_client.delete_object.side_effect = minio_storage.S3Error(404)

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
    fake_client.head_object.return_value = None

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
    fake_client.head_object.side_effect = minio_storage.S3Error(404)

    with (
        patch.object(minio_storage, "_client", return_value=fake_client),
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage.settings, "MINIO_BUCKET", "social-media"),
    ):
        result = await minio_storage.object_exists("missing/file.txt")
    assert result is False


# ── endpoint / client / bucket helpers ────────────────────────────────


def test_endpoint_and_client_and_bucket():
    s = minio_storage.settings
    with (
        patch.object(s, "MINIO_ENDPOINT", "m:9000"),
        patch.object(s, "MINIO_SECURE", True),
        patch.object(s, "MINIO_ACCESS_KEY", "ak"),
        patch.object(s, "MINIO_SECRET_KEY", "sk"),
        patch.object(s, "MINIO_BUCKET", "b"),
    ):
        assert minio_storage._endpoint() == "https://m:9000"
        c = minio_storage._client()
        assert c is not None
        assert minio_storage._bucket() == "b"

    # missing creds -> None
    with (
        patch.object(s, "MINIO_ENDPOINT", "m:9000"),
        patch.object(s, "MINIO_ACCESS_KEY", ""),
        patch.object(s, "MINIO_SECRET_KEY", "sk"),
    ):
        assert minio_storage._client() is None

    with patch.object(s, "MINIO_BUCKET", ""):
        assert minio_storage._bucket() == "social-media"


def test_ensure_bucket_paths():
    minio_storage._state["bucket_ready"] = False

    # no client -> False
    with patch.object(minio_storage, "_client", return_value=None):
        assert minio_storage.ensure_bucket() is False

    # head fails, create succeeds -> True
    c = MagicMock()
    c.head_bucket.side_effect = Exception("no bucket")
    with patch.object(minio_storage, "_client", return_value=c):
        assert minio_storage.ensure_bucket() is True
        c.create_bucket.assert_called_once()

    # cached -> short-circuit
    assert minio_storage.ensure_bucket() is True

    minio_storage._state["bucket_ready"] = False
    c2 = MagicMock()
    c2.head_bucket.side_effect = Exception("x")
    c2.create_bucket.side_effect = Exception("denied")
    with patch.object(minio_storage, "_client", return_value=c2):
        assert minio_storage.ensure_bucket() is False
    minio_storage._state["bucket_ready"] = False


# ── get / delete / exists error branches ─────────────────────────────


@pytest.mark.asyncio
async def test_get_object_errors():
    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        with pytest.raises(Exception) as e:
            await minio_storage.get_object("k")
        assert "not configured" in str(e.value)

    c = MagicMock()
    from app.services.s3_sigv4 import S3Error
    c.get_object.side_effect = S3Error(404, "missing")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        with pytest.raises(Exception) as e:
            await minio_storage.get_object("k")
        assert getattr(e.value, "status_code", None) == 404

    c.get_object.side_effect = S3Error(500, "upstream")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        with pytest.raises(Exception) as e:
            await minio_storage.get_object("k")
        assert getattr(e.value, "status_code", None) == 502

    c.get_object.side_effect = RuntimeError("weird")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        with pytest.raises(Exception) as e:
            await minio_storage.get_object("k")
        assert getattr(e.value, "status_code", None) == 502


@pytest.mark.asyncio
async def test_get_object_ok_and_delete_and_exists():
    c = MagicMock()
    c.get_object.return_value = b"bytes"
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.get_object("k") == b"bytes"

    # delete paths
    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        assert await minio_storage.delete_object("k") is False

    c.delete_object.return_value = None
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.delete_object("k") is True

    from app.services.s3_sigv4 import S3Error
    c.delete_object.side_effect = S3Error(404, "gone")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.delete_object("k") is True

    c.delete_object.side_effect = S3Error(500, "x")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        with pytest.raises(Exception) as e:
            await minio_storage.delete_object("k")
        assert getattr(e.value, "status_code", None) == 502

    c.delete_object.side_effect = RuntimeError("x")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        with pytest.raises(Exception):
            await minio_storage.delete_object("k")


@pytest.mark.asyncio
async def test_object_exists_branches():
    c = MagicMock()
    c.head_object.return_value = None
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.object_exists("k") is True

    from app.services.s3_sigv4 import S3Error
    c.head_object.side_effect = S3Error(404, "no")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.object_exists("k") is False

    c.head_object.side_effect = S3Error(500, "x")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.object_exists("k") is False

    c.head_object.side_effect = RuntimeError("x")
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
    ):
        assert await minio_storage.object_exists("k") is False

    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        assert await minio_storage.object_exists("k") is False


# ── upload public_url variants ────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_public_url_variants():
    c = MagicMock()
    c.put_object.return_value = "e"
    s = minio_storage.settings
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
        patch.object(s, "MEDIA_PUBLIC_BASE_URL", "https://cdn.io/"),
    ):
        out = await minio_storage.upload_object("k", b"d")
        assert out["public_url"] == "https://cdn.io/api/v1/media/view?path=k"
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
        patch.object(s, "MEDIA_PUBLIC_BASE_URL", ""),
    ):
        out = await minio_storage.upload_object("k", b"d")
        assert out["public_url"] == "/api/v1/media/view?path=k"


# ── presigned URLs ────────────────────────────────────────────────────


def test_presigned_urls():
    c = MagicMock()
    c.access_key = "ak"
    c.secret_key = "sk"
    c.region = "us-east-1"
    s = minio_storage.settings
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
        patch.object(minio_storage, "presign_url", return_value="https://signed"),
        patch.object(s, "MINIO_ENDPOINT", "m:9000"),
        patch.object(s, "MINIO_SECURE", False),
        patch.object(s, "MINIO_BUCKET", "b"),
        patch.object(s, "MEDIA_PUBLIC_BASE_URL", "https://cdn.io"),
    ):
        out = minio_storage.presigned_upload_url("t1", "photo.png", "image/png", 100)
        assert out["upload_url"] == "https://signed"
        assert out["public_url"].startswith("https://cdn.io/")
        assert "t1/" in out["key"]

        assert minio_storage.presigned_download_url("k") == "https://signed"

    # unconfigured -> None
    with patch.object(minio_storage, "ensure_bucket", return_value=False):
        assert minio_storage.presigned_upload_url("t", "f.png", "i", 1) is None
        assert minio_storage.presigned_download_url("k") is None

    # ensure ok but client None -> None
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=None),
    ):
        assert minio_storage.presigned_upload_url("t", "f.png", "i", 1) is None
        assert minio_storage.presigned_download_url("k") is None


def test_team_key():
    key = minio_storage._team_key("team1", "photo.JPG", "image/jpeg")
    parts = key.split("/")
    assert parts[0] == "team1"
    assert key.endswith(".JPG")
    key2 = minio_storage._team_key("t", "noext", "application/octet-stream")
    assert key2.endswith(".bin")


def test_ensure_bucket_head_success():
    minio_storage._state["bucket_ready"] = False
    c = MagicMock()
    c.head_bucket.return_value = None
    with patch.object(minio_storage, "_client", return_value=c):
        assert minio_storage.ensure_bucket() is True
        c.create_bucket.assert_not_called()
    minio_storage._state["bucket_ready"] = False


def test_presigned_upload_no_base_url():
    c = MagicMock()
    c.access_key = "ak"
    c.secret_key = "sk"
    c.region = "us-east-1"
    s = minio_storage.settings
    with (
        patch.object(minio_storage, "ensure_bucket", return_value=True),
        patch.object(minio_storage, "_client", return_value=c),
        patch.object(minio_storage, "presign_url", return_value="https://signed"),
        patch.object(s, "MINIO_ENDPOINT", "m:9000"),
        patch.object(s, "MINIO_SECURE", False),
        patch.object(s, "MINIO_BUCKET", "b"),
        patch.object(s, "MEDIA_PUBLIC_BASE_URL", ""),
    ):
        out = minio_storage.presigned_upload_url("t1", "f.png", "image/png", 10)
        assert out["public_url"] == f"/api/v1/media/view?path={out['key']}"
