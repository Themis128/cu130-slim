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
    assert "id,username,followers_count" in fake.calls[0]["params"]["fields"]


@pytest.mark.asyncio
async def test_create_image_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012357"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_image_container(
            "https://example.com/image.jpg",
            caption="Hello Instagram",
        )

    assert result == "17890012357"
    assert fake.calls[0]["url"].endswith("/media")
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/image.jpg"
    assert fake.calls[0]["data"]["caption"] == "Hello Instagram"


@pytest.mark.asyncio
async def test_create_video_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012358"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_video_container(
            "https://example.com/video.mp4",
            caption="Reel caption",
        )

    assert result == "17890012358"
    assert fake.calls[0]["data"]["video_url"] == "https://example.com/video.mp4"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["caption"] == "Reel caption"


@pytest.mark.asyncio
async def test_create_carousel_item_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012359"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_carousel_item("https://example.com/carousel-1.jpg")

    assert result == "17890012359"
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/carousel-1.jpg"
    assert fake.calls[0]["data"]["is_carousel_item"] == "true"


@pytest.mark.asyncio
async def test_create_carousel_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012360"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_carousel_container(
            ["111111", "222222"],
            caption="Carousel caption",
        )

    assert result == "17890012360"
    assert fake.calls[0]["data"]["media_type"] == "CAROUSEL"
    assert fake.calls[0]["data"]["children"] == "111111,222222"
    assert fake.calls[0]["data"]["caption"] == "Carousel caption"


@pytest.mark.asyncio
async def test_publish_container_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012361"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.publish_container("123456")

    assert result == "17890012361"
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


# ── Feature 1: Publishing quota check ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_publishing_limit_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": [{
                "quota_usage": [
                    {"metric": "publish_count", "quota_duration_seconds": 86400, "value": 3}
                ],
                "config": {"quota_total": 25},
            }]
        })
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_publishing_limit()

    assert result["data"][0]["config"]["quota_total"] == 25
    assert result["data"][0]["quota_usage"][0]["value"] == 3
    assert "/content_publishing_limit" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_get_remaining_publish_quota(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": [{
                "quota_usage": [
                    {"metric": "publish_count", "value": 10}
                ],
                "config": {"quota_total": 25},
            }]
        })
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        remaining = await client.get_remaining_publish_quota()

    assert remaining == 15  # 25 - 10


@pytest.mark.asyncio
async def test_get_remaining_publish_quota_fails_open(client):
    """If the API fails, should return 25 (full quota) rather than blocking."""
    fake = _FakeAsyncClient(_FakeResponse(500, "error"))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        remaining = await client.get_remaining_publish_quota()

    assert remaining == 25


# ── Feature 2: Comment management ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_comments_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": [
                {"id": "17890012346", "text": "Great post!", "username": "user1", "like_count": 5},
                {"id": "17890012347", "text": "Nice", "username": "user2", "like_count": 1},
            ]
        })
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.list_comments("123456")

    assert len(result["data"]) == 2
    assert result["data"][0]["text"] == "Great post!"
    assert "/123456/comments" in fake.calls[0]["url"]
    assert "instagram_manage_comments" not in fake.calls[0]["params"]  # scope is in token


@pytest.mark.asyncio
async def test_list_comments_invalid_limit(client):
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        await client.list_comments("123", limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        await client.list_comments("123", limit=101)


@pytest.mark.asyncio
async def test_reply_to_comment_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"id": "17890012348", "text": "Thank you!", "username": "testuser"})
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.reply_to_comment("17890012345", "Thank you!")

    assert result["id"] == "17890012348"
    assert "/17890012345/replies" in fake.calls[0]["url"]
    assert fake.calls[0]["data"]["message"] == "Thank you!"


@pytest.mark.asyncio
async def test_reply_to_comment_empty_message(client):
    with pytest.raises(ValueError, match="message is required"):
        await client.reply_to_comment("17890012345", "")


@pytest.mark.asyncio
async def test_hide_comment_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"hidden": "true"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.hide_comment("17890012345", hide=True)

    assert result["hidden"] == "true"
    assert fake.calls[0]["data"]["hidden"] == "true"


@pytest.mark.asyncio
async def test_delete_comment_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"success": True}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.delete_comment("17890012345")

    assert result is True
    assert fake.calls[0]["method"] == "DELETE"


# ── Feature 3: Auto-poll container status ─────────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_container_ready_finished(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status_code": "FINISHED"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        status = await client.wait_for_container_ready("17890012345", timeout=5.0, interval=0.1)

    assert status == "FINISHED"


@pytest.mark.asyncio
async def test_wait_for_container_ready_error(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status_code": "ERROR"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.InstagramAPIError, match="ERROR"):
            await client.wait_for_container_ready("17890012345", timeout=5.0, interval=0.1)


@pytest.mark.asyncio
async def test_wait_for_container_ready_timeout(client):
    # Always returns IN_PROGRESS — will time out.
    # Provide enough responses for the polling loop (0.5s / 0.1s = ~5 polls).
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"status_code": "IN_PROGRESS"}) for _ in range(20)
    ])
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(TimeoutError, match="did not finish"):
            await client.wait_for_container_ready("17890012345", timeout=0.5, interval=0.1)


@pytest.mark.asyncio
async def test_create_and_publish_video_one_call(client):
    # Three responses: container create, status check, publish
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17890012349"}),
        _FakeResponse(200, {"status_code": "FINISHED"}),
        _FakeResponse(200, {"id": "17890012356"}),
    ])
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.create_and_publish_video(
            "https://example.com/video.mp4",
            caption="Test reel",
            timeout=5.0,
        )

    assert media_id == "17890012356"


# ── Feature 4: Story builder ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_story_container_image(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012350"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_story_container(
            media_url="https://example.com/story.jpg",
            media_type="IMAGE",
            link="https://cloudless.gr",
            alt_text="Cloudless story",
        )

    assert result == "17890012350"
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["image_url"] == "https://example.com/story.jpg"
    assert fake.calls[0]["data"]["link"] == "https://cloudless.gr"
    assert fake.calls[0]["data"]["alt_text"] == "Cloudless story"


@pytest.mark.asyncio
async def test_create_story_container_video(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "17890012351"}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_story_container(
            media_url="https://example.com/story.mp4",
            media_type="VIDEO",
        )

    assert result == "17890012351"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["video_url"] == "https://example.com/story.mp4"


@pytest.mark.asyncio
async def test_create_story_container_invalid_type(client):
    with pytest.raises(ValueError, match="media_type must be 'IMAGE' or 'VIDEO'"):
        await client.create_story_container("https://example.com/x", media_type="CAROUSEL")


@pytest.mark.asyncio
async def test_publish_story_image_one_call(client):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17890012352"}),
        _FakeResponse(200, {"id": "17890012354"}),
    ])
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.publish_story(
            media_url="https://example.com/story.jpg",
            media_type="IMAGE",
            link="https://cloudless.gr",
        )

    assert media_id == "17890012354"


@pytest.mark.asyncio
async def test_publish_story_video_with_polling(client):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17890012353"}),
        _FakeResponse(200, {"status_code": "FINISHED"}),
        _FakeResponse(200, {"id": "17890012355"}),
    ])
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.publish_story(
            media_url="https://example.com/story.mp4",
            media_type="VIDEO",
            timeout=5.0,
        )

    assert media_id == "17890012355"


# ── Feature 5: Mentions tracking (tagged_media) ──────────────────────────


@pytest.mark.asyncio
async def test_get_tagged_media_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": [
                {
                    "id": "m1",
                    "caption": "Loving @cloudless.gr!",
                    "media_type": "IMAGE",
                    "permalink": "https://instagram.com/p/abc123",
                    "username": "fan_user",
                    "timestamp": "2026-09-01T10:00:00+0000",
                }
            ]
        })
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_tagged_media()

    assert len(result["data"]) == 1
    assert result["data"][0]["caption"] == "Loving @cloudless.gr!"
    assert "/tagged_media" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_get_recent_mentions_flat_list(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": [
                {"id": "m1", "caption": "Mention 1"},
                {"id": "m2", "caption": "Mention 2"},
            ]
        })
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        mentions = await client.get_recent_mentions(limit=5)

    assert len(mentions) == 2
    assert mentions[0]["id"] == "m1"


@pytest.mark.asyncio
async def test_get_recent_mentions_invalid_limit(client):
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        await client.get_recent_mentions(limit=0)
