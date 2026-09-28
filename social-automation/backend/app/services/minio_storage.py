"""MinIO S3-compatible local object storage.

Acts as a local failover between Cloudflare R2 and local disk. Uses the
stdlib SigV4 client in app.services.s3_sigv4 (no AWS SDK). The bucket is
auto-created on first use if it does not exist.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from app.core.config import get_settings
from app.services.s3_sigv4 import S3Error, S3LiteClient, presign_url

logger = logging.getLogger(__name__)
settings = get_settings()

_state: dict[str, bool] = {"bucket_ready": False}


def _endpoint() -> str:
    endpoint = (settings.MINIO_ENDPOINT or "").strip()
    scheme = "https" if settings.MINIO_SECURE else "http"
    return f"{scheme}://{endpoint}"


def _client() -> S3LiteClient | None:
    access_key = (settings.MINIO_ACCESS_KEY or "").strip()
    secret_key = (settings.MINIO_SECRET_KEY or "").strip()
    if not all([settings.MINIO_ENDPOINT, access_key, secret_key]):
        return None
    return S3LiteClient(
        _endpoint(), access_key, secret_key, region="us-east-1"
    )


def _bucket() -> str:
    return (settings.MINIO_BUCKET or "social-media").strip()


def ensure_bucket() -> bool:
    """Create the MinIO bucket if it does not exist. Returns True if usable."""
    if _state["bucket_ready"]:
        return True

    client = _client()
    if not client:
        return False

    bucket = _bucket()
    try:
        client.head_bucket(bucket)
        _state["bucket_ready"] = True
        return True
    except Exception:
        pass

    try:
        client.create_bucket(bucket)
        logger.info("MinIO bucket '%s' created", bucket)
        _state["bucket_ready"] = True
        return True
    except Exception as exc:
        logger.warning("MinIO bucket creation failed: %s", exc)
        return False


def minio_enabled() -> bool:
    """Return True if MinIO credentials and endpoint are configured."""
    return all([
        (settings.MINIO_ENDPOINT or "").strip(),
        (settings.MINIO_ACCESS_KEY or "").strip(),
        (settings.MINIO_SECRET_KEY or "").strip(),
    ])


async def upload_object(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    metadata: dict | None = None,
) -> dict:
    """Upload an object to MinIO.

    Returns ``{"etag", "size", "public_url", "key"}``.
    """
    if not ensure_bucket():
        raise HTTPException(status_code=500, detail="MinIO is not configured or unreachable")

    client = _client()
    assert client is not None
    etag = client.put_object(_bucket(), key, data, content_type, metadata)

    # Route through the API /view endpoint so the browser can reach the object
    # without needing direct access to the internal MinIO hostname.
    base = (settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        public_url = f"{base}/api/v1/media/view?path={key}"
    else:
        # Use a relative URL that works regardless of the host/domain.
        public_url = f"/api/v1/media/view?path={key}"

    return {
        "key": key,
        "etag": etag,
        "size": len(data),
        "public_url": public_url,
    }


async def get_object(key: str) -> bytes:
    """Download an object from MinIO."""
    if not ensure_bucket():
        raise HTTPException(status_code=500, detail="MinIO is not configured or unreachable")

    client = _client()
    assert client is not None

    try:
        return client.get_object(_bucket(), key)
    except S3Error as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=f"MinIO object not found: {key}") from exc
        raise HTTPException(status_code=502, detail=f"MinIO fetch failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO fetch failed: {exc}") from exc


async def delete_object(key: str) -> bool:
    """Delete an object from MinIO. Returns True if deleted or not found."""
    if not ensure_bucket():
        return False

    client = _client()
    assert client is not None

    try:
        client.delete_object(_bucket(), key)
        return True
    except S3Error as exc:
        if exc.status == 404:
            return True
        raise HTTPException(status_code=502, detail=f"MinIO delete failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO delete failed: {exc}") from exc


async def object_exists(key: str) -> bool:
    """Check if an object exists in MinIO."""
    if not ensure_bucket():
        return False

    client = _client()
    assert client is not None

    try:
        client.head_object(_bucket(), key)
        return True
    except S3Error as exc:
        if exc.status == 404:
            return False
        return False
    except Exception:
        return False


def presigned_upload_url(
    team_id,
    filename: str,
    mime_type: str,
    size_bytes: int,
    expiry: int = 300,
) -> dict | None:
    """Return a presigned PUT URL for direct browser upload to MinIO.

    Returns ``{"key", "upload_url", "public_url"}`` or ``None`` if MinIO
    is not configured.
    """
    if not ensure_bucket():
        return None

    client = _client()
    if not client:
        return None

    key = _team_key(team_id, filename, mime_type)
    url = presign_url(
        "PUT",
        f"{_endpoint()}/{_bucket()}/{key}",
        access_key=client.access_key,
        secret_key=client.secret_key,
        region=client.region,
        expires=expiry,
        headers={"content-type": mime_type, "content-length": str(size_bytes)},
    )

    base = (settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        public_url = f"{base}/api/v1/media/view?path={key}"
    else:
        public_url = f"/api/v1/media/view?path={key}"

    return {
        "key": key,
        "upload_url": url,
        "public_url": public_url,
    }


def presigned_download_url(key: str, expiry: int = 3600) -> str | None:
    """Return a presigned GET URL for a MinIO object."""
    if not ensure_bucket():
        return None

    client = _client()
    if not client:
        return None

    return presign_url(
        "GET",
        f"{_endpoint()}/{_bucket()}/{key}",
        access_key=client.access_key,
        secret_key=client.secret_key,
        region=client.region,
        expires=expiry,
    )


def _team_key(team_id, filename: str, mime_type: str) -> str:
    """Generate a team-scoped storage key."""
    now = datetime.now(UTC)
    date_part = now.strftime("%Y/%m/%d")
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    safe = f"{uuid.uuid4().hex[:16]}.{ext}"
    return f"{team_id}/{date_part}/{safe}"
