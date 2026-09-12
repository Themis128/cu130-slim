from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.tasks.linkedin_session_check import _send_alert


class TestSendAlertCooldown:
    @pytest.mark.asyncio
    async def test_alert_skipped_within_cooldown(self):
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.linkedin_session_check.async_sessionmaker",
            return_value=mock_factory,
        ), patch(
            "app.worker.tasks.linkedin_session_check.create_async_engine",
            return_value=MagicMock(),
        ), patch(
            "app.services.email_templates.send_linkedin_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.linkedin_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "expired")

        mock_send.assert_not_called()
        mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_sent_when_no_recent_alert(self):
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.linkedin_session_check.async_sessionmaker",
            return_value=mock_factory,
        ), patch(
            "app.worker.tasks.linkedin_session_check.create_async_engine",
            return_value=MagicMock(),
        ), patch(
            "app.services.email_templates.send_linkedin_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.linkedin_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "expired")

        mock_send.assert_called_once()
        mock_slack.assert_awaited_once()

