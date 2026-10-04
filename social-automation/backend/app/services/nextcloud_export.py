"""Mirror media assets to the omv Nextcloud workspace via WebDAV.

Generated and uploaded assets land under
``<NEXTCLOUD_EXPORT_ROOT>/media/<date-folder>/<filename>`` on
``cloud.cloudless.gr`` so every SocialAuto asset is reachable from the
self-hosted workspace (desktop sync, mobile apps, Talk shares).

Auth uses a Nextcloud app password — never the account login password.
Uploads run in a Celery task so API latency is unaffected.
"""

from __future__ import annotations

import logging
import os
import posixpath
import uuid
from urllib.parse import quote

import aiofiles
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.path_utils import safe_resolve
from app.models.content import MediaAsset, StorageBackend
from app.services import minio_storage, r2_storage

logger = logging.getLogger(__name__)

settings = get_settings()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")


def nextcloud_export_enabled() -> bool:
    return bool(
        settings.NEXTCLOUD_EXPORT_ENABLED
        and settings.NEXTCLOUD_DAV_URL.strip()
        and settings.NEXTCLOUD_USERNAME.strip()
        and settings.NEXTCLOUD_APP_PASSWORD.strip()
    )


def _dav_base() -> str:
    return settings.NEXTCLOUD_DAV_URL.rstrip("/")


def _q(path: str) -> str:
    return "/".join(quote(part) for part in path.strip("/").split("/") if part)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        auth=(settings.NEXTCLOUD_USERNAME, settings.NEXTCLOUD_APP_PASSWORD),
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": "socialauto-nextcloud-export/1.0"},
    )


async def _ensure_dirs(client: httpx.AsyncClient, remote_dir: str) -> None:
    """MKCOL each path segment; 405 = collection already exists."""
    path = ""
    for part in remote_dir.strip("/").split("/"):
        path = f"{path}/{part}"
        resp = await client.request("MKCOL", f"{_dav_base()}/{_q(path)}")
        if resp.status_code not in (201, 405):
            resp.raise_for_status()


async def upload_bytes(
    remote_path: str,
    data: bytes,
    mime_type: str = "application/octet-stream",
) -> str:
    """Upload bytes under ``NEXTCLOUD_EXPORT_ROOT/<remote_path>``.

    ``remote_path`` may contain folders — they are created as needed.
    Returns the full remote path (root included).
    """
    root = settings.NEXTCLOUD_EXPORT_ROOT.strip("/")
    full = posixpath.join(root, remote_path.strip("/"))
    remote_dir = posixpath.dirname(full)

    async with _client() as client:
        await _ensure_dirs(client, remote_dir)
        resp = await client.put(
            f"{_dav_base()}/{_q(full)}",
            content=data,
            headers={"Content-Type": mime_type},
        )
        resp.raise_for_status()
    return full


async def create_public_share(remote_path: str, password: str | None = None) -> str | None:
    """Create a Nextcloud public link share for a file/folder.

    Uses the OCS Share API (files_sharing app). Returns the public URL.
    """
    root = settings.NEXTCLOUD_EXPORT_ROOT.strip("/")
    path = remote_path if remote_path.startswith(f"/{root}") else f"/{remote_path}"
    ocs_url = settings.NEXTCLOUD_DAV_URL.split("/remote.php/dav")[0].rstrip("/")
    ocs_url = f"{ocs_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    payload: dict[str, str | int] = {
        "path": path,
        "shareType": 3,  # public link
        "permissions": 1,  # read-only
    }
    if password:
        payload["password"] = password
    async with _client() as client:
        resp = await client.post(
            ocs_url,
            data=payload,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        )
        resp.raise_for_status()
        return (resp.json().get("ocs", {}).get("data", {}) or {}).get("url")


async def _read_asset_bytes(asset: MediaAsset) -> bytes:
    if asset.storage_backend == StorageBackend.r2:
        return await r2_storage.get_object(asset.storage_path)
    if asset.storage_backend == StorageBackend.minio:
        return await minio_storage.get_object(asset.storage_path)
    abs_path = safe_resolve(UPLOAD_DIR, asset.storage_path)
    async with aiofiles.open(abs_path, "rb") as f:
        return await f.read()


async def export_media_asset(
    asset_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict:
    """Mirror one media asset to Nextcloud and record the remote path."""
    async with session_factory() as db:
        asset = (await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))).scalar_one_or_none()
        if asset is None:
            return {"status": "skipped", "reason": "asset not found"}

        data = await _read_asset_bytes(asset)
        remote = await upload_bytes(
            f"media/{asset.storage_path}",
            data,
            asset.mime_type or "application/octet-stream",
        )
        asset.meta_data = {**(asset.meta_data or {}), "nextcloud_path": remote}
        await db.commit()
        logger.info("nextcloud export: %s → %s (%d bytes)", asset.id, remote, len(data))
        return {"status": "exported", "remote_path": remote, "bytes": len(data)}
