from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.slack_notifications as sn


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


@pytest.mark.asyncio
async def test_post_digest_prefers_webhook_when_set():
    settings = SimpleNamespace(
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="C123",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, "ok"))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        ok, err, ts = await sn.post_digest_text_to_slack("hello")

    assert ok is True
    assert err is None
    assert ts is None
    assert fake.calls[0]["url"] == settings.SLACK_WEBHOOK_URL
    assert fake.calls[0]["json"] == {"text": "hello"}


@pytest.mark.asyncio
async def test_post_digest_falls_back_to_token_and_channel():
    settings = SimpleNamespace(
        SLACK_WEBHOOK_URL="",
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="C999",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "ts": "123.456"}))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        ok, err, ts = await sn.post_digest_text_to_slack("digest")

    assert ok is True
    assert err is None
    assert ts is not None
    assert fake.calls[0]["url"] == "https://api.slack.com/api/chat.postMessage"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer xoxb-test"
    assert fake.calls[0]["json"]["channel"] == "C999"
    assert fake.calls[0]["json"]["text"] == "digest"


@pytest.mark.asyncio
async def test_post_alert_is_non_fatal_when_not_configured():
    settings = SimpleNamespace(
        SLACK_ALERTS_WEBHOOK_URL="",
        SLACK_ALERTS_CHANNEL_ID="",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
    )
    with patch.object(sn, "get_settings", return_value=settings):
        # Should not raise.
        await sn.post_alert_to_slack("alert text")


@pytest.mark.asyncio
async def test_post_alert_uses_alerts_webhook():
    settings = SimpleNamespace(
        SLACK_ALERTS_WEBHOOK_URL="https://hooks.slack.test/alerts",
        SLACK_ALERTS_CHANNEL_ID="",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, "ok"))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        await sn.post_alert_to_slack("boom")

    assert fake.calls[0]["url"] == settings.SLACK_ALERTS_WEBHOOK_URL
    assert fake.calls[0]["json"] == {"text": "boom"}

