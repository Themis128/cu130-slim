"""Cloudflare R2 S3-compatible presigned URL helpers.

SigV4 presigning is done in-process via app.services.s3_sigv4 (pure stdlib,
no AWS SDK). Objects can still be uploaded server-side via r2_storage.py
using the Cloudflare REST API when the S3 credentials are not configured.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.s3_sigv4 import presign_url

settings = get_settings()


def _r2_creds() -> tuple[str, str] | None:
    access_key = (settings.R2_ACCESS_KEY_ID or "").strip()
    secret_key = (settings.R2_SECRET_ACCESS_KEY or "").strip()
    if not all([settings.CLOUDFLARE_ACCOUNT_ID, access_key, secret_key]):
        return None
    return access_key, secret_key


def _r2_host() -> str:
    endpoint = (settings.R2_S3_ENDPOINT or "").strip()
    if endpoint:
        return endpoint.split("://", 1)[-1].rstrip("/")
    return f"{settings.CLOUDFLARE_ACCOUNT_ID.strip()}.r2.cloudflarestorage.com"


def _team_key(team_id, filename: str, mime_type: str) -> str:
    now = datetime.now(UTC)
    date_part = now.strftime("%Y/%m/%d")
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    safe = f"{uuid.uuid4().hex[:16]}.{ext}"
    return f"{team_id}/{date_part}/{safe}"


def _public_url(key: str) -> str | None:
    public = (settings.R2_PUBLIC_URL or "").strip()
    if not public:
        return None
    if not public.endswith("/"):
        public += "/"
    return f"{public}{key}"


def presigned_upload_url(
    team_id,
    filename: str,
    mime_type: str,
    size_bytes: int,
    expiry: int = 300,
) -> dict | None:
    """Return a presigned PUT URL for direct browser upload to R2.

    Returns ``{"key", "upload_url", "public_url"}`` or ``None`` if S3
    credentials are not configured.
    """
    creds = _r2_creds()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not creds or not bucket:
        return None

    key = _team_key(team_id, filename, mime_type)
    url = presign_url(
        "PUT",
        f"https://{_r2_host()}/{bucket}/{key}",
        access_key=creds[0],
        secret_key=creds[1],
        region="auto",
        expires=expiry,
        headers={"content-type": mime_type, "content-length": str(size_bytes)},
    )
    return {
        "key": key,
        "upload_url": url,
        "public_url": _public_url(key),
    }


def presigned_download_url(key: str, expiry: int = 3600) -> str | None:
    """Return a presigned GET URL for an R2 object, or the public URL if set."""
    public = _public_url(key)
    if public:
        return public

    creds = _r2_creds()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not creds or not bucket:
        return None

    return presign_url(
        "GET",
        f"https://{_r2_host()}/{bucket}/{key}",
        access_key=creds[0],
        secret_key=creds[1],
        region="auto",
        expires=expiry,
    )
