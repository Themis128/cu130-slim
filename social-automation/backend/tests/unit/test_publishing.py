"""Unit tests for the platform publishing pipeline."""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.services import publishing as pub


class _FakeResponse:
    def __init__(self, status_code: int, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("response body is not JSON")

    @property
    def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,
                response=self,
            )


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in that records requests and returns preset responses."""

    def __init__(self, responses):
        if not isinstance(responses, list):
            responses = [responses]
        self._responses = list(responses)
        self._call_index = 0
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, params=None, data=None, json=None, content=None):
        self.calls.append({
            "method": "POST",
            "url": url,
            "headers": headers,
            "params": params,
            "data": data,
            "json": json,
            "content": content,
        })
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.fixture
def account():
    return SimpleNamespace(account_id="user-456", username="testuser")


@pytest.fixture
def post():
    return SimpleNamespace()


@pytest.mark.asyncio
async def test_publish_threads_text_only(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "12345"}),
        _FakeResponse(200, {"id": "67890"}),
    ])

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello Threads!", account, post, [])

    assert result.success is True
    assert result.platform_post_id == "67890"
    assert result.platform_url == "https://www.threads.net/@testuser/post/67890"
    assert len(fake.calls) == 2
    assert fake.calls[0]["data"]["media_type"] == "TEXT"
    assert fake.calls[0]["data"]["text"] == "Hello Threads!"
    assert fake.calls[1]["data"]["creation_id"] == "12345"


@pytest.mark.asyncio
async def test_publish_threads_image(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "45678"}),
        _FakeResponse(200, {"id": "78901"}),
    ])

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello image!", account, post, ["/tmp/img.png"])

    assert result.success is True
    assert result.platform_post_id == "78901"
    assert result.platform_url == "https://www.threads.net/@testuser/post/78901"
    assert len(fake.calls) == 2
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["image_url"] == "https://cdn.example.com/img.png"
    assert fake.calls[1]["data"]["creation_id"] == "45678"


@pytest.mark.asyncio
async def test_publish_threads_access_denied(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(_FakeResponse(403, {"error": {"message": "Access denied"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello!", account, post, ["/tmp/img.png"])

    assert result.success is False
    assert "Threads API access denied" in result.error
