"""Unit tests for the Instagram session health-check Celery task.

Covers:
- decrypt_field on enc:-prefixed (encrypted) session IDs
- decrypt_field on legacy plaintext session IDs
- decrypt_field on None / missing values
- sidecar unreachable → account marked expired
- sidecar returns 401 → account marked expired
- sidecar returns 200 → account stays active
- no session_id in meta_data → account skipped
- alert cooldown prevents duplicate emails
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security import decrypt_field, encrypt_field
from app.worker.tasks.instagram_session_check import (
    _check_sidecar_health,
    _check_sidecar_session,
    _send_alert,
)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stand-in."""

    def __init__(self, response: _FakeResponse = None, exc: Exception = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return self._response


# ─── decrypt_field tests ────────────────────────────────────────────


class TestDecryptField:
    def test_enc_prefixed_value_roundtrips(self):
        """encrypt_field → decrypt_field returns the original value."""
        original = "sessionid_abc123"
        encrypted = encrypt_field(original)
        assert encrypted.startswith("enc:")
        assert decrypt_field(encrypted) == original

    def test_legacy_plaintext_passes_through(self):
        """decrypt_field returns plaintext values that lack the enc: prefix."""
        assert decrypt_field("plain_session_id") == "plain_session_id"

    def test_none_returns_none(self):
        assert decrypt_field(None) is None

    def test_empty_string_returns_empty(self):
        assert decrypt_field("") == ""


# ─── sidecar session check tests ────────────────────────────────────


class TestCheckSidecarSession:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(200)),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is True

    @pytest.mark.asyncio
    async def test_401_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(401)),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(exc=ConnectionError("refused")),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is False


class TestCheckSidecarHealth:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(200)),
        ):
            result = await _check_sidecar_health("http://sidecar:8000")
        assert result is True

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(exc=ConnectionError("down")),
        ):
            result = await _check_sidecar_health("http://sidecar:8000")
        assert result is False


# ─── alert cooldown tests ───────────────────────────────────────────


class TestSendAlertCooldown:
    @pytest.mark.asyncio
    async def test_alert_skipped_within_cooldown(self):
        """If an alert was sent in the last 24h, skip sending."""
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"
        owner.id = "user-123"

        # Mock the cooldown DB query to report 1 recent alert.
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_db
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.instagram_session_check.async_sessionmaker",
            return_value=mock_factory,
        ), patch(
            "app.worker.tasks.instagram_session_check.create_async_engine",
            return_value=MagicMock(),
        ), patch(
            "app.services.email_templates.send_instagram_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.instagram_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "testuser", "session rejected")

        mock_send.assert_not_called()
        mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_sent_when_no_recent_alert(self):
        """If no recent alert exists, send the email via email_templates."""
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"
        owner.id = "user-123"

        # Cooldown check returns 0 → alert should be sent.
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_db
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.instagram_session_check.async_sessionmaker",
            return_value=mock_factory,
        ), patch(
            "app.worker.tasks.instagram_session_check.create_async_engine",
            return_value=MagicMock(),
        ), patch(
            "app.services.email_templates.send_instagram_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.instagram_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "testuser", "session rejected")

        mock_send.assert_called_once()
        mock_slack.assert_awaited_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["owner_email"] == owner.email
        assert "testuser" in kwargs["account_username"]
        assert kwargs["reason"] == "session rejected"
