"""Unit tests for R2 presigned URL helpers (stdlib SigV4 signer)."""
from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app.services import r2_presigned

_SIG_PARAMS = {
    "X-Amz-Algorithm",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-Signature",
    "X-Amz-SignedHeaders",
}


def _r2_settings(mock_settings):
    mock_settings.CLOUDFLARE_ACCOUNT_ID = "account"
    mock_settings.R2_ACCESS_KEY_ID = "key"
    mock_settings.R2_SECRET_ACCESS_KEY = "secret"
    mock_settings.R2_BUCKET_NAME = "bucket"
    mock_settings.R2_PUBLIC_URL = ""
    mock_settings.R2_S3_ENDPOINT = ""


def test_presigned_upload_url_returns_none_without_credentials():
    """If S3 credentials are missing, the function returns None."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.CLOUDFLARE_ACCOUNT_ID = ""
        mock_settings.R2_ACCESS_KEY_ID = ""
        mock_settings.R2_SECRET_ACCESS_KEY = ""
        result = r2_presigned.presigned_upload_url("team-1", "pic.png", "image/png", 1234)
    assert result is None


def test_presigned_upload_url_returns_upload_url():
    """A real SigV4 presigned PUT URL is returned (no SDK)."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        _r2_settings(mock_settings)
        mock_settings.R2_PUBLIC_URL = "https://cdn.example.com/"
        result = r2_presigned.presigned_upload_url("team-1", "pic.png", "image/png", 1234)

    assert result is not None
    parts = urlsplit(result["upload_url"])
    assert parts.scheme == "https"
    assert parts.netloc == "account.r2.cloudflarestorage.com"
    assert parts.path == f"/bucket/{result['key']}"
    query = parse_qs(parts.query)
    assert _SIG_PARAMS <= set(query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Credential"] == ["key/" + query["X-Amz-Date"][0][:8] + "/auto/s3/aws4_request"]
    # ContentType + ContentLength are signed headers — client must send them
    assert "content-type" in query["X-Amz-SignedHeaders"][0]
    assert "content-length" in query["X-Amz-SignedHeaders"][0]
    assert result["public_url"].startswith("https://cdn.example.com/")


def test_presigned_download_url_returns_public_url_when_configured():
    """If R2_PUBLIC_URL is set, the public URL is returned instead of presigned."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.R2_PUBLIC_URL = "https://cdn.example.com/"
        url = r2_presigned.presigned_download_url("team-1/pic.png")
    assert url == "https://cdn.example.com/team-1/pic.png"


def test_presigned_download_url_signs_get():
    """Without a public base, a presigned GET URL is generated in-process."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        _r2_settings(mock_settings)
        url = r2_presigned.presigned_download_url("team-1/pic.png")

    assert url is not None
    parts = urlsplit(url)
    assert parts.netloc == "account.r2.cloudflarestorage.com"
    assert parts.path == "/bucket/team-1/pic.png"
    query = parse_qs(parts.query)
    assert _SIG_PARAMS <= set(query)
    assert query["X-Amz-SignedHeaders"] == ["host"]


def test_presigned_url_is_deterministic_per_minute():
    """Two calls in the same second produce identical signatures."""
    from app.services.s3_sigv4 import presign_url

    a = presign_url("GET", "https://h.example.com/b/k", "ak", "sk")
    b = presign_url("GET", "https://h.example.com/b/k", "ak", "sk")
    assert a == b
