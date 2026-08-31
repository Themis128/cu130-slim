"""Unit tests for the Threads REST API client."""

import httpx
import pytest
from unittest.mock import patch

from app.services import threads_api as api


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
    """httpx.AsyncClient stand-in that records last request and returns a preset response."""

    def __init__(self, responses: list[_FakeResponse] | _FakeResponse | None = None):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return self._next_response()

    async def post(self, url, headers=None, data=None, json=None, content=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data, "json": json, "content": content})
        return self._next_response()

    async def put(self, url, headers=None, content=None):
        self.calls.append({"method": "PUT", "url": url, "headers": headers, "content": content})
        return self._next_response()

    async def delete(self, url, headers=None, params=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers, "params": params})
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.fixture
def client():
    return api.ThreadsAPIClient(access_token="tok-123", user_id="user-456")


@pytest.mark.asyncio
async def test_validate_token_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "user-456", "username": "test_user"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await client.validate_token()

    assert result["id"] == "user-456"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == f"{api.THREADS_API_BASE}/v1.0/me"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"
    assert fake.calls[0]["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_get_insights_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"values": [{"value": 42}]}]}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await client.get_insights("views")

    assert result["data"][0]["values"][0]["value"] == 42
    assert fake.calls[0]["method"] == "GET"
    assert f"{api.THREADS_API_BASE}/v1.0/user-456/insights" == fake.calls[0]["url"]
    assert fake.calls[0]["params"]["metric"] == "views"


@pytest.mark.asyncio
async def test_create_text_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "creation-1"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        creation_id = await client.create_text_container("Hello Threads!")

    assert creation_id == "creation-1"
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["data"]["media_type"] == "TEXT"
    assert fake.calls[0]["data"]["text"] == "Hello Threads!"


@pytest.mark.asyncio
async def test_create_image_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "creation-2"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        creation_id = await client.create_image_container("https://example.com/img.png", "Nice image")

    assert creation_id == "creation-2"
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/img.png"
    assert fake.calls[0]["data"]["text"] == "Nice image"


@pytest.mark.asyncio
async def test_create_video_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "creation-3"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        creation_id = await client.create_video_container("https://example.com/video.mp4", "Watch this")

    assert creation_id == "creation-3"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["video_url"] == "https://example.com/video.mp4"
    assert fake.calls[0]["data"]["text"] == "Watch this"


@pytest.mark.asyncio
async def test_create_carousel_item_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "creation-item-1"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        creation_id = await client.create_carousel_item("https://example.com/slide1.png")

    assert creation_id == "creation-item-1"
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/slide1.png"
    assert fake.calls[0]["data"]["is_carousel_item"] == "true"


@pytest.mark.asyncio
async def test_create_carousel_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "creation-carousel-1"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        creation_id = await client.create_carousel_container(["item-1", "item-2"], "My carousel")

    assert creation_id == "creation-carousel-1"
    assert fake.calls[0]["data"]["media_type"] == "CAROUSEL"
    assert fake.calls[0]["data"]["children"] == "item-1,item-2"
    assert fake.calls[0]["data"]["text"] == "My carousel"


@pytest.mark.asyncio
async def test_publish_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "media-123"}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        media_id = await client.publish_container("12345")

    assert media_id == "media-123"
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"] == f"{api.THREADS_API_BASE}/v1.0/user-456/threads_publish"
    assert fake.calls[0]["data"]["creation_id"] == "12345"


@pytest.mark.asyncio
async def test_delete_post_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        ok = await client.delete_post("987654321")

    assert ok is True
    assert fake.calls[0]["method"] == "DELETE"
    assert fake.calls[0]["url"] == f"{api.THREADS_API_BASE}/v1.0/987654321"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_delete_post_failure_returns_false(client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"error": {"message": "Cannot delete"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        ok = await client.delete_post("987654321")

    assert ok is False


@pytest.mark.asyncio
async def test_4xx_raises_threads_api_error(client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"error": {"message": "Bad request"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        with pytest.raises(api.ThreadsAPIError) as exc_info:
            await client.create_text_container("Bad post")

    assert exc_info.value.status_code == 400
    assert "Bad request" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_5xx_maps_to_502(client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"error": {"message": "Internal error"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        with pytest.raises(api.ThreadsAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 502
    assert "Internal error" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_503_504_maps_to_503(client):
    fake = _FakeAsyncClient(_FakeResponse(504, {"error": {"message": "Gateway timeout"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        with pytest.raises(api.ThreadsAPIError) as exc_info:
            await client.get_insights()

    assert exc_info.value.status_code == 503
    assert "Gateway timeout" in exc_info.value.response_text
