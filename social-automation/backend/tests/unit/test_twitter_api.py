"""Unit tests for the Twitter/X REST API client."""

import httpx
import pytest
from unittest.mock import patch

from app.services import twitter_api as api


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

    async def post(self, url, headers=None, json=None, content=None, files=None, data=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "json": json,
                "content": content,
                "files": files,
                "data": data,
            }
        )
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
    return api.TwitterAPIClient(access_token="tok-123")


@pytest.mark.asyncio
async def test_validate_token_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"id": "12345", "name": "Test User"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.validate_token()

    assert result["data"]["id"] == "12345"
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/me"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_validate_token_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"status": 401, "title": "Unauthorized"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 401
    assert "https://api.x.com/2/users/me" in exc_info.value.url


@pytest.mark.asyncio
async def test_validate_token_5xx_maps_to_502(client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"status": 500, "title": "Internal error"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_create_tweet_success(client):
    fake = _FakeAsyncClient(_FakeResponse(201, {"data": {"id": "111", "text": "Hello"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_tweet("Hello")

    assert result["data"]["id"] == "111"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets"
    assert fake.calls[0]["json"]["text"] == "Hello"
    assert "media" not in fake.calls[0]["json"]


@pytest.mark.asyncio
async def test_create_tweet_with_media(client):
    fake = _FakeAsyncClient(_FakeResponse(201, {"data": {"id": "222"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        await client.create_tweet("With media", media_ids=["m1", "m2"])

    payload = fake.calls[0]["json"]
    assert payload["media"]["media_ids"] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_create_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(403, {"status": 403, "detail": "Forbidden"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.create_tweet("Hello")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_tweet_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"deleted": True}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.delete_tweet("1234567890")

    assert result is True
    assert fake.calls[0]["method"] == "DELETE"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets/1234567890"


@pytest.mark.asyncio
async def test_delete_tweet_not_found_returns_false(client):
    fake = _FakeAsyncClient(_FakeResponse(404, {"errors": [{"message": "Not found"}]}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.delete_tweet("1234567890")

    assert result is False


@pytest.mark.asyncio
async def test_delete_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"status": 401, "title": "Unauthorized"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.delete_tweet("1234567890")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_tweet_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": {
                    "id": "1234567890",
                    "text": "A tweet",
                    "public_metrics": {"like_count": 5},
                }
            },
        )
    )

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_tweet("1234567890")

    assert result["data"]["id"] == "1234567890"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets/1234567890"
    assert fake.calls[0]["params"]["tweet.fields"] == "created_at,public_metrics,entities"


@pytest.mark.asyncio
async def test_get_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(404, {"status": 404, "detail": "Not found"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.get_tweet("1234567890")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_upload_media_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"media_id_string": "m-123"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.upload_media(b"image-bytes", media_category="tweet_image")

    assert media_id == "m-123"
    assert fake.calls[0]["url"] == api.TWITTER_MEDIA_UPLOAD_URL
    assert fake.calls[0]["files"]["media"] == b"image-bytes"
    assert fake.calls[0]["data"]["media_category"] == "tweet_image"


@pytest.mark.asyncio
async def test_upload_media_raises_when_no_id_returned(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.upload_media(b"image-bytes")

    assert "Media upload returned no media_id" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_upload_media_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"errors": [{"message": "Invalid media"}]}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.upload_media(b"image-bytes")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_user_tweets_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [{"id": "1", "text": "tweet 1"}],
                "meta": {"result_count": 1},
            },
        )
    )

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_user_tweets("12345", max_results=5)

    assert result["data"][0]["id"] == "1"
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/12345/tweets"
    assert fake.calls[0]["params"]["max_results"] == 5


@pytest.mark.asyncio
async def test_get_user_tweets_invalid_max_results(client):
    with pytest.raises(ValueError):
        await client.get_user_tweets("12345", max_results=200)
