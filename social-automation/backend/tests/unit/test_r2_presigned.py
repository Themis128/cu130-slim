"""Unit tests for R2 presigned URL helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import r2_presigned


def test_presigned_upload_url_returns_none_without_credentials():
    """If S3 credentials are missing, the function returns None."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.CLOUDFLARE_ACCOUNT_ID = ""
        mock_settings.R2_ACCESS_KEY_ID = ""
        mock_settings.R2_SECRET_ACCESS_KEY = ""
        result = r2_presigned.presigned_upload_url("team-1", "pic.png", "image/png", 1234)
    assert result is None


def test_presigned_upload_url_returns_upload_url():
    """When credentials are set, a presigned PUT URL is returned."""
    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://example.r2.cloudflarestorage.com/team-1/..."

    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.CLOUDFLARE_ACCOUNT_ID = "account"
        mock_settings.R2_ACCESS_KEY_ID = "key"
        mock_settings.R2_SECRET_ACCESS_KEY = "secret"
        mock_settings.R2_BUCKET_NAME = "bucket"
        mock_settings.R2_PUBLIC_URL = "https://cdn.example.com/"
        mock_settings.R2_S3_ENDPOINT = ""

        with patch("app.services.r2_presigned.boto3.client", return_value=fake_client) as mock_boto:
            result = r2_presigned.presigned_upload_url("team-1", "pic.png", "image/png", 1234)

    assert result is not None
    assert result["upload_url"] == "https://example.r2.cloudflarestorage.com/team-1/..."
    assert result["public_url"].startswith("https://cdn.example.com/")
    mock_boto.assert_called_once()
    call_kwargs = mock_boto.call_args.kwargs
    assert call_kwargs["endpoint_url"] == "https://account.r2.cloudflarestorage.com"


def test_presigned_download_url_returns_public_url_when_configured():
    """If R2_PUBLIC_URL is set, the public URL is returned instead of presigned."""
    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.R2_PUBLIC_URL = "https://cdn.example.com/"
        url = r2_presigned.presigned_download_url("team-1/pic.png")
    assert url == "https://cdn.example.com/team-1/pic.png"


def test_presigned_download_url_falls_back_to_boto():
    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://signed.example.com/"

    with patch.object(r2_presigned, "settings") as mock_settings:
        mock_settings.CLOUDFLARE_ACCOUNT_ID = "account"
        mock_settings.R2_ACCESS_KEY_ID = "key"
        mock_settings.R2_SECRET_ACCESS_KEY = "secret"
        mock_settings.R2_BUCKET_NAME = "bucket"
        mock_settings.R2_PUBLIC_URL = ""

        with patch("app.services.r2_presigned.boto3.client", return_value=fake_client):
            url = r2_presigned.presigned_download_url("team-1/pic.png")

    assert url == "https://signed.example.com/"
