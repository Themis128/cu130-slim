"""Cloudflare R2 S3-compatible presigned URL helpers.

R2 is S3-compatible, so we use boto3 only for presigned URL generation.
Objects can still be uploaded server-side via r2_storage.py using the
Cloudflare REST API when the S3 credentials are not configured.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import boto3
from botocore.config import Config

from app.core.config import get_settings

settings = get_settings()


def _s3_client():
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    access_key = (settings.R2_ACCESS_KEY_ID or "").strip()
    secret_key = (settings.R2_SECRET_ACCESS_KEY or "").strip()
    if not all([account_id, access_key, secret_key]):
        return None

    endpoint = (settings.R2_S3_ENDPOINT or "").strip()
    if not endpoint:
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


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
    client = _s3_client()
    if not client:
        return None

    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not bucket:
        return None

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

    client = _s3_client()
    if not client:
        return None

    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not bucket:
        return None

    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry,
    )
