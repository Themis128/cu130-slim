from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.worker.tasks.publishing import _notify_publish_failure


@pytest.mark.asyncio
async def test_notify_publish_failure_posts_slack_alert():
    post = SimpleNamespace(id="post-123")
    account = SimpleNamespace(platform="linkedin")
    item = SimpleNamespace(id="queue-456")

    with patch(
        "app.worker.tasks.publishing.post_alert_to_slack",
        new=AsyncMock(),
    ) as mock_post:
        await _notify_publish_failure(
            post=post,
            account=account,
            queue_item=item,
            reason="bad request",
        )

    mock_post.assert_awaited_once()
    text = mock_post.call_args.args[0]
    assert "post-123" in text
    assert "linkedin" in text
    assert "queue-456" in text

