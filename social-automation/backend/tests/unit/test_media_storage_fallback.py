"""Unit tests for the media storage fallback chain: R2 → MinIO → local disk."""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services.media_storage import StorageBackend, _store_bytes


def _make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_fallback_r2_success():
    """When R2 is enabled and succeeds, it is used."""
    fake_r2 = {"key": "2026/test.png", "etag": "abc", "size": 100, "public_url": "https://r2.dev/test.png"}
    with (
        patch("app.services.media_storage._r2_enabled", return_value=True),
        patch("app.services.media_storage._minio_enabled", return_value=True),
        patch("app.services.media_storage.r2_storage.upload_object", new=AsyncMock(return_value=fake_r2)),
    ):
        backend, path, url = await _store_bytes(_make_png(), "test.png", "image/png", "2026/08/31")
    assert backend == StorageBackend.r2
    assert url == "https://r2.dev/test.png"


@pytest.mark.asyncio
async def test_fallback_r2_fail_minio_success():
    """When R2 fails, MinIO is used."""
    from fastapi import HTTPException

    fake_minio = {"key": "2026/test.png", "etag": "def", "size": 100, "public_url": "http://minio:9000/social-media/2026/test.png"}
    with (
        patch("app.services.media_storage._r2_enabled", return_value=True),
        patch("app.services.media_storage._minio_enabled", return_value=True),
        patch("app.services.media_storage.r2_storage.upload_object", new=AsyncMock(side_effect=HTTPException(status_code=502))),
        patch("app.services.media_storage.minio_storage.upload_object", new=AsyncMock(return_value=fake_minio)),
    ):
        backend, path, url = await _store_bytes(_make_png(), "test.png", "image/png", "2026/08/31")
    assert backend == StorageBackend.minio
    assert "minio:9000" in url


@pytest.mark.asyncio
async def test_fallback_both_fail_local_disk():
    """When both R2 and MinIO fail, local disk is used."""
    from pathlib import Path

    from fastapi import HTTPException

    with (
        patch("app.services.media_storage._r2_enabled", return_value=True),
        patch("app.services.media_storage._minio_enabled", return_value=True),
        patch("app.services.media_storage.r2_storage.upload_object", new=AsyncMock(side_effect=HTTPException(status_code=502))),
        patch("app.services.media_storage.minio_storage.upload_object", new=AsyncMock(side_effect=HTTPException(status_code=500))),
        patch("app.services.media_storage.safe_resolve") as mock_resolve,
        patch("app.services.media_storage.aiofiles") as mock_aiofiles,
    ):
        fake_path = MagicMock(spec=Path)
        fake_path.relative_to.return_value = Path("2026/08/31/test.png")
        fake_path.parent.mkdir = MagicMock()
        mock_resolve.return_value = fake_path

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_aiofiles.open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
        mock_aiofiles.open.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("app.services.media_storage._public_local_url", return_value=None):
            backend, path, url = await _store_bytes(_make_png(), "test.png", "image/png", "2026/08/31")
    assert backend == StorageBackend.local


@pytest.mark.asyncio
async def test_fallback_r2_disabled_minio_success():
    """When R2 is not configured, MinIO is used directly."""
    fake_minio = {"key": "2026/test.png", "etag": "def", "size": 100, "public_url": "http://minio:9000/social-media/2026/test.png"}
    with (
        patch("app.services.media_storage._r2_enabled", return_value=False),
        patch("app.services.media_storage._minio_enabled", return_value=True),
        patch("app.services.media_storage.minio_storage.upload_object", new=AsyncMock(return_value=fake_minio)),
    ):
        backend, path, url = await _store_bytes(_make_png(), "test.png", "image/png", "2026/08/31")
    assert backend == StorageBackend.minio


@pytest.mark.asyncio
async def test_fallback_all_disabled_local_disk():
    """When neither R2 nor MinIO is configured, local disk is used."""
    from pathlib import Path

    with (
        patch("app.services.media_storage._r2_enabled", return_value=False),
        patch("app.services.media_storage._minio_enabled", return_value=False),
        patch("app.services.media_storage.safe_resolve") as mock_resolve,
        patch("app.services.media_storage.aiofiles") as mock_aiofiles,
    ):
        fake_path = MagicMock(spec=Path)
        fake_path.relative_to.return_value = Path("2026/08/31/test.png")
        fake_path.parent.mkdir = MagicMock()
        mock_resolve.return_value = fake_path

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_aiofiles.open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
        mock_aiofiles.open.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("app.services.media_storage._public_local_url", return_value=None):
            backend, path, url = await _store_bytes(_make_png(), "test.png", "image/png", "2026/08/31")
    assert backend == StorageBackend.local
