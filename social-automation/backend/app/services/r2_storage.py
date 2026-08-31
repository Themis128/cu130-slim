"""Cloudflare R2 object storage client using the Cloudflare REST API.

Uses only the free Cloudflare services:
- R2 bucket storage (10 GB free, free egress)
- Cloudflare Workers AI for vision/text tasks

Auth requires a Cloudflare API token with the permissions:
- Account:Cloudflare R2:Edit
- Zone:Read (if using a custom R2 public domain)

Reference:
- https://developers.cloudflare.com/r2/
- https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.core.config import get_settings

settings = get_settings()

# R2 object keys must be safe path segments — no traversal, no protocol, no control chars.
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]{0,1023}$")


def _validate_key(key: str) -> str:
    """Validate an R2 object key to prevent path traversal and SSRF.

    Keys must be relative paths containing only alphanumerics, dots, hyphens,
    underscores, and forward slashes. No leading slash, no '..', no protocol.
    """
    if not key:
        raise HTTPException(status_code=400, detail="R2 object key is empty")
    if key.startswith("/"):
        raise HTTPException(status_code=400, detail="R2 object key must not start with '/'")
    if ".." in key.split("/"):
        raise HTTPException(status_code=400, detail="R2 object key contains '..' path traversal")
    if not _SAFE_KEY_RE.match(key):
        raise HTTPException(status_code=400, detail="R2 object key contains invalid characters")
    return key


def _r2_object_url(key: str) -> str | None:
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    if not bucket or not account_id:
        return None
    base = (settings.R2_API_BASE or "https://api.cloudflare.com/client/v4").rstrip("/")
    encoded_key = quote(key, safe="/")
    return f"{base}/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded_key}"


def _r2_public_url(key: str) -> str | None:
    public = (settings.R2_PUBLIC_URL or "").strip()
    if not public:
        return None
    if not public.endswith("/"):
        public += "/"
    return f"{public}{key}"


async def upload_object(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    metadata: dict | None = None,
) -> dict:
    """Upload an object to R2 via the Cloudflare REST API.

    Returns ``{"etag", "size", "public_url", "key"}``. Max single upload 300 MB.
    """
    key = _validate_key(key)
    url = _r2_object_url(key)
    if not url:
        raise HTTPException(status_code=500, detail="R2 is not configured (R2_BUCKET_NAME or CLOUDFLARE_ACCOUNT_ID missing)")

    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="CLOUDFLARE_API_TOKEN is not configured for R2 uploads")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(url, headers=headers, content=data)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"R2 upload failed ({resp.status_code}): {resp.text[:500]}",
        )

    result = resp.json().get("result") or {}
    return {
        "key": key,
        "etag": result.get("etag", ""),
        "size": len(data),
        "public_url": _r2_public_url(key),
    }


async def get_object(key: str) -> bytes:
    """Download an object from R2."""
    key = _validate_key(key)
    url = _r2_object_url(key)
    if not url:
        raise HTTPException(status_code=500, detail="R2 is not configured")

    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"R2 object not found: {key}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"R2 fetch failed ({resp.status_code}): {resp.text[:500]}")

    return resp.content


async def delete_object(key: str) -> bool:
    """Delete an object from R2. Returns True if deleted or not found."""
    key = _validate_key(key)
    url = _r2_object_url(key)
    if not url:
        return False

    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="CLOUDFLARE_API_TOKEN is not configured for R2 deletions")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.delete(url, headers={"Authorization": f"Bearer {token}"})

    if resp.status_code in (200, 404):
        return True
    raise HTTPException(status_code=502, detail=f"R2 delete failed ({resp.status_code}): {resp.text[:500]}")


async def object_exists(key: str) -> bool:
    """Check if an object exists in R2."""
    key = _validate_key(key)
    url = _r2_object_url(key)
    if not url:
        return False

    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.head(url, headers=headers, follow_redirects=True)

    return resp.status_code == 200
