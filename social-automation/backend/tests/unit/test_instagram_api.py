"""Unit tests for the Instagram Graph API client."""

from unittest.mock import patch

import httpx
import pytest

from app.services import instagram_api as api


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

    def __init__(self, responses=None):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return self._next_response()

    async def post(self, url, headers=None, data=None, json=None, content=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "data": data,
                "json": json,
                "content": content,
            }
        )
        return self._next_response()

    async def put(self, url, headers=None, content=None):
        self.calls.append({"method": "PUT", "url": url, "headers": headers, "content": content})
        return self._next_response()

    async def delete(self, url, headers=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers})
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.fixture
def client():
    return api.InstagramAPIClient(access_token="tok-123", ig_user_id="987654321")


@pytest.mark.asyncio
async def test_validate_token_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "page-1", "name": "Test Page"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.validate_token()

    assert result["id"] == "page-1"
    assert fake.calls[0]["method"] == "GET"
    assert "/me" in fake.calls[0]["url"]
    assert fake.calls[0]["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_get_profile_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "id": "987654321",
                "username": "testuser",
                "account_type": "BUSINESS",
            },
        )
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_profile()

    assert result["username"] == "testuser"
    assert fake.calls[0]["url"] == f"{api.INSTAGRAM_API_BASE}/{api.INSTAGRAM_DEFAULT_API_VERSION}/987654321"
    assert "id,username,account_type" in fake.calls[0]["params"]["fields"]


@pytest.mark.asyncio
async def test_create_image_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "image-container-1"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_image_container(
            "https://example.com/image.jpg",
            caption="Hello Instagram",
        )

    assert result == "image-container-1"
    assert fake.calls[0]["url"].endswith("/media")
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/image.jpg"
    assert fake.calls[0]["data"]["caption"] == "Hello Instagram"


@pytest.mark.asyncio
async def test_create_video_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "video-container-1"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_video_container(
            "https://example.com/video.mp4",
            caption="Reel caption",
        )

    assert result == "video-container-1"
    assert fake.calls[0]["data"]["video_url"] == "https://example.com/video.mp4"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["caption"] == "Reel caption"


@pytest.mark.asyncio
async def test_create_carousel_item_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "carousel-item-1"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_carousel_item("https://example.com/carousel-1.jpg")

    assert result == "carousel-item-1"
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/carousel-1.jpg"
    assert fake.calls[0]["data"]["is_carousel_item"] == "true"


@pytest.mark.asyncio
async def test_create_carousel_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "carousel-container-1"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_carousel_container(
            ["111111", "222222"],
            caption="Carousel caption",
        )

    assert result == "carousel-container-1"
    assert fake.calls[0]["data"]["media_type"] == "CAROUSEL"
    assert fake.calls[0]["data"]["children"] == "111111,222222"
    assert fake.calls[0]["data"]["caption"] == "Carousel caption"


@pytest.mark.asyncio
async def test_publish_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "published-media-1"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.publish_container("123456")

    assert result == "published-media-1"
    assert fake.calls[0]["url"].endswith("/media_publish")
    assert fake.calls[0]["data"]["creation_id"] == "123456"


@pytest.mark.asyncio
async def test_check_container_status_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status_code": "FINISHED"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.check_container_status("12345")

    assert result == "FINISHED"
    assert fake.calls[0]["url"] == f"{api.INSTAGRAM_API_BASE}/{api.INSTAGRAM_DEFAULT_API_VERSION}/12345"
    assert fake.calls[0]["params"]["fields"] == "status_code"


@pytest.mark.asyncio
async def test_get_media_insights_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"name": "impressions", "values": []}]}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_media_insights("333")

    assert "data" in result
    assert fake.calls[0]["url"].endswith("/333/insights")


@pytest.mark.asyncio
async def test_get_account_insights_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"name": "reach", "values": []}]}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_account_insights(metric="reach", period="week")

    assert result["data"][0]["name"] == "reach"
    assert fake.calls[0]["url"].endswith("/insights")
    assert fake.calls[0]["params"]["metric"] == "reach"
    assert fake.calls[0]["params"]["period"] == "week"


@pytest.mark.asyncio
async def test_api_error_4xx_raises_instagram_api_error(client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"error": "bad request"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.InstagramAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 400
    assert "graph.facebook.com" in exc_info.value.url


@pytest.mark.asyncio
async def test_api_error_5xx_maps_to_502(client):
    fake = _FakeAsyncClient(_FakeResponse(500, "upstream error"))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.InstagramAPIError) as exc_info:
            await client.get_profile()

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_api_error_503_maps_to_503(client):
    fake = _FakeAsyncClient(_FakeResponse(503, "service unavailable"))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.InstagramAPIError) as exc_info:
            await client.get_account_insights()

    assert exc_info.value.status_code == 503
