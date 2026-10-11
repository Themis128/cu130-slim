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

    async def post(self, url, headers=None, data=None, json=None,
                   content=None, params=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "data": data,
                "json": json,
                "content": content,
                "params": params,
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
    assert fake.calls[0]["data"]["media_type"] == "STORIES"
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
    assert fake.calls[0]["data"]["media_type"] == "STORIES"
    assert fake.calls[0]["data"]["video_url"] == "https://example.com/story.mp4"


@pytest.mark.asyncio
async def test_create_story_container_invalid_type(client):
    with pytest.raises(ValueError, match="media_type must be 'IMAGE' or 'VIDEO'"):
        await client.create_story_container("https://example.com/x", media_type="CAROUSEL")


@pytest.mark.asyncio
async def test_publish_story_image_one_call(client):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17890012352"}),
        _FakeResponse(200, {"status_code": "FINISHED"}),
        _FakeResponse(200, {"id": "17890012354"}),
    ])
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.publish_story(
            media_url="https://example.com/story.jpg",
            media_type="IMAGE",
            link="https://cloudless.gr",
            timeout=5.0,
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


# ── Discovery + audience additions (v26) ─────────────────────────────


@pytest.mark.asyncio
async def test_quota_parser_handles_int_usage(client):
    """quota_usage arrives as a bare int on v26 — used to undercount."""
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": [{"quota_usage": 2}]})
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        remaining = await client.get_remaining_publish_quota()
    assert remaining == 23


@pytest.mark.asyncio
async def test_quota_parser_handles_list_usage(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {
                        "quota_usage": [{"metric": "publish_count", "value": 7}],
                        "config": {"quota_total": 25},
                    }
                ]
            },
        )
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        remaining = await client.get_remaining_publish_quota()
    assert remaining == 18


@pytest.mark.asyncio
async def test_search_hashtag(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": [{"id": "1784", "name": "automation"}]})
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        data = await client.search_hashtag("#automation")
    call = fake.calls[0]
    assert call["url"].endswith("/ig_hashtag_search")
    assert call["params"]["q"] == "automation"
    assert call["params"]["user_id"] == "987654321"
    assert data["data"][0]["id"] == "1784"


@pytest.mark.asyncio
async def test_search_hashtag_requires_query(client):
    with pytest.raises(ValueError, match="query is required"):
        await client.search_hashtag("  ")


@pytest.mark.asyncio
async def test_hashtag_top_media(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": [{"id": "m1", "permalink": "https://i/p/1"}]})
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        rows = await client.get_hashtag_top_media("1784")
    assert rows[0]["permalink"] == "https://i/p/1"
    assert "/1784/top_media" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_business_discovery(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"business_discovery": {"username": "rival", "followers_count": 5000}})
    )
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        data = await client.business_discovery("@rival")
    assert data["business_discovery"]["followers_count"] == 5000
    assert "business_discovery.username(rival)" in fake.calls[0]["params"]["fields"]


@pytest.mark.asyncio
async def test_follower_demographics_breakdown(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"name": "follower_demographics"}]}))
    with patch("app.services.instagram_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        await client.get_follower_demographics(breakdown="country")
    params = fake.calls[0]["params"]
    assert params["metric"] == "follower_demographics"
    assert params["breakdown"] == "country"
    assert params["metric_type"] == "total_value"

    with pytest.raises(ValueError, match="breakdown"):
        await client.get_follower_demographics(breakdown="bogus")


# ── additional coverage: dm surfaces, webdm client, misc branches ───


@pytest.mark.asyncio
async def test_get_me_and_validation(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "1", "user_id": "2"}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.get_me()
    assert out["user_id"] == "2"
    assert "user_id" in fake.calls[0]["params"]["fields"]

    with pytest.raises(ValueError):
        api._validate_id("")
    with pytest.raises(ValueError):
        api._validate_id("bad id")


@pytest.mark.asyncio
async def test_container_validation_branches(client):
    with pytest.raises(ValueError):
        await client.create_image_container("", "cap")
    with pytest.raises(ValueError):
        await client.create_video_container("")
    with pytest.raises(ValueError):
        await client.create_carousel_item("")
    with pytest.raises(ValueError):
        await client.create_carousel_container([], "cap")
    with pytest.raises(ValueError):
        await client.create_carousel_container(
            [str(i) for i in range(11)], "cap")
    with pytest.raises(ValueError):
        await client.create_carousel_container(["bad id!"], "cap")

    # no-id responses → InstagramAPIError
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(api.InstagramAPIError, match="no id"):
            await client.create_image_container("https://x/i.jpg", "c")
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(api.InstagramAPIError, match="no id"):
            await client.create_carousel_container(["1", "2"], "c")


@pytest.mark.asyncio
async def test_story_container_branches(client):
    with pytest.raises(ValueError):
        await client.create_story_container("")
    with pytest.raises(ValueError):
        await client.create_story_container("https://x/m",
                                            media_type="GIF")

    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "c1"}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        cid = await client.create_story_container(
            "https://x/v.mp4", media_type="VIDEO",
            link="https://l", alt_text="alt")
        assert cid == "c1"
        data = fake.calls[0]["data"]
        assert data["media_type"] == "STORIES"
        assert data["video_url"] == "https://x/v.mp4"
        assert data["link"] == "https://l" and data["alt_text"] == "alt"

    # publish_story wrapper
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "s1"}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with patch.object(client, "wait_for_container_ready",
                          return_value="FINISHED"):
            with patch.object(client, "publish_container",
                              return_value="media1"):
                out = await client.publish_story("https://x/i.jpg")
    assert out == "media1"


@pytest.mark.asyncio
async def test_hashtag_discovery_and_demographics(client):
    fake = _FakeAsyncClient(_FakeResponse(
        200, {"data": [{"id": "m1"}]}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.get_hashtag_recent_media("123")
        assert out == [{"id": "m1"}]
        assert "/123/recent_media" in fake.calls[0]["url"]

    with pytest.raises(ValueError):
        await client.business_discovery("")
    fake = _FakeAsyncClient(_FakeResponse(200, {"business_discovery": {}}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.business_discovery("@competitor")
        assert "business_discovery" in out
        assert "competitor" in fake.calls[0]["params"]["fields"]

    with pytest.raises(ValueError):
        await client.get_engaged_audience_demographics(breakdown="zip")
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": []}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        await client.get_engaged_audience_demographics(
            breakdown="city")
        assert fake.calls[0]["params"]["breakdown"] == "city"
        assert fake.calls[0]["params"]["metric"] == \
            "engaged_audience_demographics"

    with pytest.raises(ValueError):
        await client.get_account_insights(metric="")


@pytest.mark.asyncio
async def test_tagged_media_and_mentions(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"t": 1}]}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.get_tagged_media()
        assert out["data"] == [{"t": 1}]
        assert "tagged_media" in fake.calls[0]["url"]
    with pytest.raises(ValueError):
        await client.get_recent_mentions(limit=0)

    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"m": 1}]}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.get_recent_mentions()
        assert out == [{"m": 1}]


@pytest.mark.asyncio
async def test_graph_dm_methods(client):
    fake = _FakeAsyncClient([_FakeResponse(200, {"ok": 1})] * 6)
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        await client.send_dm("r1", "hello")
        send = fake.calls[0]
        assert "/messages" in send["url"]
        assert send["json"]["recipient"]["id"] == "r1"
        assert send["json"]["messaging_type"] == "RESPONSE"

        await client.send_dm_template("r1", "tmpl",
                                      components=[{"type": "body"}])
        tmpl = fake.calls[1]
        assert tmpl["json"]["messaging_type"] == "MESSAGE_TAG"
        att = tmpl["json"]["message"]["attachment"]["payload"]
        assert att["name"] == "tmpl" and att["components"]

        await client.get_conversations(limit=5)
        await client.get_dm_messages("c1", limit=10)
        await client.mark_dm_read("c1", recipient_id="r2")
        await client.send_typing_indicator("r1")
    assert len(fake.calls) == 6


# ── InstagramWebDMClient ────────────────────────────────────────────


def _webdm():
    return api.InstagramWebDMClient(
        session_id="sess", csrf_token="csrf", ds_user_id="u1")


def test_webdm_headers():
    c = _webdm()
    h = c._headers()
    assert h["x-csrftoken"] == "csrf"
    assert "sessionid=sess" in h["cookie"]
    assert "ds_user_id=u1" in h["cookie"]

    with patch.object(api.httpx, "AsyncClient") as ac:
        c = api.InstagramWebDMClient(session_id="s", proxy="http://p")
        c._client()
        assert ac.call_args.kwargs["proxy"] == "http://p"


@pytest.mark.asyncio
async def test_webdm_conversations():
    c = _webdm()
    body = {"inbox": {"threads": [{
        "thread_id": "t1",
        "users": [{"pk": 5, "username": "alice"},
                  {"username": "bob"}]}]}}
    fake = _FakeAsyncClient(_FakeResponse(200, body))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await c.get_conversations(limit=5)
    conv = out["data"][0]
    assert conv["id"] == "t1"
    names = [p["name"] for p in conv["participants"]["data"]]
    assert names == ["alice", "bob"]

    fake = _FakeAsyncClient(_FakeResponse(401, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(api.InstagramAPIError):
            await c.get_conversations()


@pytest.mark.asyncio
async def test_webdm_messages():
    c = _webdm()
    body = {"thread": {"items": [
        {"item_id": "i1", "user_id": 9, "text": "hi",
         "timestamp": "1"},
        {"item_id": "i2", "user_id": 9, "share_text": "shared"},
        {"item_id": "i3", "user_id": 9},
    ]}}
    fake = _FakeAsyncClient(_FakeResponse(200, body))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await c.get_dm_messages("t1")
    msgs = out["data"]
    assert len(msgs) == 2
    assert msgs[0]["message"] == "hi"
    assert msgs[1]["message"] == "shared"

    fake = _FakeAsyncClient(_FakeResponse(404, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(api.InstagramAPIError):
            await c.get_dm_messages("t1")


@pytest.mark.asyncio
async def test_webdm_send_and_read():
    c = _webdm()
    fake = _FakeAsyncClient([_FakeResponse(200, {"status": "ok"})] * 2)
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await c.send_dm("u1", "yo")
        assert out["status"] == "ok"
        data = fake.calls[0]["data"]
        assert data["text"] == "yo" and "u1" in data["recipient_users"]

        out = await c.mark_dm_read("t1")
        assert out == {"status": "ok"}

    fake = _FakeAsyncClient(_FakeResponse(500, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        with pytest.raises(api.InstagramAPIError):
            await c.send_dm("u1", "x")
    fake = _FakeAsyncClient(_FakeResponse(500, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        assert await c.mark_dm_read("t1") == {"status": "error"}

    assert await c.send_typing_indicator("u1") == {"status": "ok"}


@pytest.mark.asyncio
async def test_last_edges(client):
    # safe_detail
    e4 = api.InstagramAPIError(403, "x", "https://u")
    assert e4.safe_detail == "Instagram API request failed (403)"
    e5 = api.InstagramAPIError(500, "x", "https://u")
    assert "temporarily unavailable" in e5.safe_detail

    # empty token
    with pytest.raises(ValueError, match="access token"):
        api.InstagramAPIClient(access_token="", ig_user_id="1")

    # no-id branches for video/carousel_item/publish
    for method, args in [
        (client.create_video_container, ("https://x/v.mp4",)),
        (client.create_carousel_item, ("https://x/i.jpg",)),
        (client.publish_container, ("12345",)),
        (client.create_story_container, ("https://x/i.jpg",)),
    ]:
        fake = _FakeAsyncClient(_FakeResponse(200, {}))
        with patch.object(api.httpx, "AsyncClient", return_value=fake):
            with pytest.raises(api.InstagramAPIError, match="no id"):
                await method(*args)

    # list_recent_media
    fake = _FakeAsyncClient(_FakeResponse(
        200, {"data": [{"id": "m1"}]}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        out = await client.list_recent_media(limit=5)
        assert out == [{"id": "m1"}]
        assert "permalink" in fake.calls[0]["params"]["fields"]

    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch.object(api.httpx, "AsyncClient", return_value=fake):
        assert await client.list_recent_media() == []
