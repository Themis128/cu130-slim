"""Unit tests for the Facebook Graph API client."""

from unittest.mock import patch

import httpx
import pytest

from app.services import facebook_api as api


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

    async def post(self, url, headers=None, params=None, json=None, data=None, content=None):
        self.calls.append({
            "method": "POST",
            "url": url,
            "headers": headers,
            "params": params,
            "json": json,
            "data": data,
            "content": content,
        })
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
    return api.FacebookAPIClient(access_token="tok-123", page_id="123456789")


@pytest.mark.asyncio
async def test_validate_token(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "me-1", "name": "Test User"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.validate_token()

    assert result["id"] == "me-1"
    assert result["name"] == "Test User"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == f"{api.FACEBOOK_GRAPH_BASE}/{api.DEFAULT_API_VERSION}/me"
    assert fake.calls[0]["params"]["fields"] == "id,name"
    assert fake.calls[0]["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_get_pages(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {
                        "id": "111",
                        "name": "Page One",
                        "access_token": "page-token-1",
                        "category": "Community",
                    }
                ]
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        pages = await client.get_pages()

    assert len(pages) == 1
    assert pages[0]["id"] == "111"
    assert pages[0]["name"] == "Page One"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"].endswith("/me/accounts")
    assert fake.calls[0]["params"]["fields"] == "id,name,access_token,category,perms"


@pytest.mark.asyncio
async def test_exchange_long_lived_token(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"access_token": "long-lived-token"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        token = await client.exchange_long_lived_token("client-id", "client-secret")

    assert token == "long-lived-token"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/oauth/access_token")
    assert call["params"]["grant_type"] == "fb_exchange_token"
    assert call["params"]["client_id"] == "client-id"
    assert call["params"]["client_secret"] == "client-secret"
    assert call["params"]["fb_exchange_token"] == "tok-123"


@pytest.mark.asyncio
async def test_exchange_long_lived_token_missing_token_raises(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(ValueError, match="long-lived access token"):
            await client.exchange_long_lived_token("client-id", "client-secret")


@pytest.mark.asyncio
async def test_get_long_lived_page_tokens(client):
    me_resp = _FakeResponse(200, {"id": "user-42", "name": "Test User"})
    accounts_resp = _FakeResponse(
        200,
        {
            "data": [
                {"id": "222", "name": "Page Two", "access_token": "page-token-2"}
            ]
        },
    )
    fake = _FakeAsyncClient([me_resp, accounts_resp])
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        pages = await client.get_long_lived_page_tokens("long-user-token")

    assert len(pages) == 1
    assert pages[0]["id"] == "222"
    assert pages[0]["access_token"] == "page-token-2"
    assert fake.calls[0]["url"].endswith("/me")
    assert fake.calls[0]["params"]["access_token"] == "long-user-token"
    assert fake.calls[1]["url"].endswith("/user-42/accounts")
    assert fake.calls[1]["params"]["access_token"] == "long-user-token"


@pytest.mark.asyncio
async def test_create_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "post-1"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_post("Hello Facebook!", "https://example.com")

    assert result["id"] == "post-1"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/feed")
    assert call["data"]["message"] == "Hello Facebook!"
    assert call["data"]["link"] == "https://example.com"
    assert call["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_create_photo_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "photo-1", "post_id": "post-2"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_photo_post("https://example.com/photo.jpg", "My caption")

    assert result["id"] == "photo-1"
    assert result["post_id"] == "post-2"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/photos")
    assert call["data"]["url"] == "https://example.com/photo.jpg"
    assert call["data"]["caption"] == "My caption"


@pytest.mark.asyncio
async def test_create_video_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "video-1"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_video_post("https://example.com/video.mp4", "My description")

    assert result["id"] == "video-1"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/videos")
    assert call["data"]["file_url"] == "https://example.com/video.mp4"
    assert call["data"]["description"] == "My description"


@pytest.mark.asyncio
async def test_get_page_insights(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {
                        "name": "page_impressions_unique",
                        "values": [{"value": 42}],
                    }
                ]
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.get_page_insights(
            metric="page_impressions_unique",
            period="day",
            since="2023-01-01",
            until="2023-01-02",
        )

    assert result["data"][0]["name"] == "page_impressions_unique"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/123456789/insights")
    assert call["params"]["metric"] == "page_impressions_unique"
    assert call["params"]["period"] == "day"
    assert call["params"]["since"] == "2023-01-01"
    assert call["params"]["until"] == "2023-01-02"


@pytest.mark.asyncio
async def test_get_post_insights(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {"name": "post_impressions", "values": [{"value": 10}]}
                ]
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.get_post_insights("987654321")

    assert result["data"][0]["name"] == "post_impressions"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/987654321/insights")
    assert call["params"]["metric"] == "post_impressions,post_engaged_users"


@pytest.mark.asyncio
async def test_delete_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"success": True}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        ok = await client.delete_post("987654321")

    assert ok is True
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/987654321")
    assert call["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_delete_post_empty_response_defaults_to_true(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        ok = await client.delete_post("987654321")

    assert ok is True


@pytest.mark.asyncio
async def test_error_handling_4xx(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            400,
            {
                "error": {
                    "message": "Invalid token",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(api.FacebookAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 400
    assert "Invalid token" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_error_handling_5xx(client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"error": {"message": "Internal server error"}}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(api.FacebookAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 502
    assert "Internal server error" in exc_info.value.response_text
