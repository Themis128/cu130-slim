"""MinIO S3-compatible local object storage.

Acts as a local failover between Cloudflare R2 and local disk. Uses boto3
with the S3 API, which MinIO implements fully. The bucket is auto-created
on first use if it does not exist.
"""
from __future__ import annotations

import logging

import boto3
from botocore.config import Config
from fastapi import HTTPException

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_state: dict[str, bool] = {"bucket_ready": False}


def _client():
    endpoint = (settings.MINIO_ENDPOINT or "").strip()
    access_key = (settings.MINIO_ACCESS_KEY or "").strip()
    secret_key = (settings.MINIO_SECRET_KEY or "").strip()
    if not all([endpoint, access_key, secret_key]):
        return None

    scheme = "https" if settings.MINIO_SECURE else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{endpoint}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
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
        client.head_bucket(Bucket=bucket)
        _state["bucket_ready"] = True
        return True
    except Exception:
        pass

    try:
        client.create_bucket(Bucket=bucket)
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
    bucket = _bucket()

    params: dict = {"Bucket": bucket, "Key": key, "Body": data, "ContentType": content_type}
    if metadata:
        params["Metadata"] = metadata

    resp = client.put_object(**params)
    etag = resp.get("ETag", "").strip('"')

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
    bucket = _bucket()

    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    except client.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail=f"MinIO object not found: {key}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO fetch failed: {exc}")


async def delete_object(key: str) -> bool:
    """Delete an object from MinIO. Returns True if deleted or not found."""
    if not ensure_bucket():
        return False

    client = _client()
    bucket = _bucket()

    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.NoSuchKey:
        return True
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO delete failed: {exc}")


async def object_exists(key: str) -> bool:
    """Check if an object exists in MinIO."""
    if not ensure_bucket():
        return False

    client = _client()
    bucket = _bucket()

    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.NoSuchKey:
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

    bucket = _bucket()
    key = _team_key(team_id, filename, mime_type)

    params = {
        "Bucket": bucket,
        "Key": key,
        "ContentType": mime_type,
        "ContentLength": size_bytes,
    }
    url = client.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=expiry,
        HttpMethod="PUT",
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

    bucket = _bucket()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry,
    )


def _team_key(team_id, filename: str, mime_type: str) -> str:
    """Generate a team-scoped storage key."""
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    date_part = now.strftime("%Y/%m/%d")
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    safe = f"{uuid.uuid4().hex[:16]}.{ext}"
    return f"{team_id}/{date_part}/{safe}"
