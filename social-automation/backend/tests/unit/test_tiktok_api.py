"""Unit tests for the TikTok Content Posting API client."""

from unittest.mock import patch

import httpx
import pytest

from app.services import tiktok_api as api


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

    async def post(self, url, headers=None, json=None, content=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json, "content": content})
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
    return api.TikTokAPIClient(access_token="tok-123", open_id="open-123")


def test_video_chunk_plan():
    assert api._video_chunk_plan(1024) == (1024, 1)
    assert api._video_chunk_plan(25_000_000) == (10 * 1024 * 1024, 2)


def _patch_client(fake: _FakeAsyncClient):
    return patch(
        "app.services.tiktok_api.httpx.AsyncClient",
        side_effect=lambda timeout=30.0, **_: fake,
    )


@pytest.mark.asyncio
async def test_validate_token_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"open_id": "open-123", "display_name": "TikTok User"}})
    )
    with _patch_client(fake):
        result = await client.validate_token()

    assert result["data"]["open_id"] == "open-123"
    assert result["data"]["display_name"] == "TikTok User"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == "https://open.tiktokapis.com/v2/user/info/"
    assert fake.calls[0]["params"] == {
        "fields": "open_id,union_id,avatar_url,display_name"
    }
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_validate_token_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(401, "unauthorized"))
    with _patch_client(fake):
        with pytest.raises(api.TikTokAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 401
    assert "user/info/" in exc_info.value.url
    assert "unauthorized" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_get_creator_info_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"max_video_duration_sec": 600}})
    )
    with _patch_client(fake):
        result = await client.get_creator_info()

    assert result["data"]["max_video_duration_sec"] == 600
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"] == (
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
    )


@pytest.mark.asyncio
async def test_get_creator_info_raises_business_error(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"error": {"code": "access_token_invalid", "message": "bad token"}})
    )
    with _patch_client(fake):
        with pytest.raises(api.TikTokAPIError) as exc_info:
            await client.get_creator_info()

    assert exc_info.value.status_code == 400
    assert "access_token_invalid" in str(exc_info.value)
    assert "bad token" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_init_video_post_pull_from_url(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"publish_id": "publish-123"}})
    )
    with _patch_client(fake):
        result = await client.init_video_post(
            source="PULL_FROM_URL",
            video_url="https://example.com/video.mp4",
            title="My TikTok",
            privacy_level="PUBLIC",
        )

    assert result["data"]["publish_id"] == "publish-123"
    assert fake.calls[0]["url"] == (
        "https://open.tiktokapis.com/v2/post/publish/video/init/"
    )
    payload = fake.calls[0]["json"]
    assert payload["post_info"]["title"] == "My TikTok"
    assert payload["post_info"]["privacy_level"] == "PUBLIC"
    assert payload["source_info"]["source"] == "PULL_FROM_URL"
    assert payload["source_info"]["video_url"] == "https://example.com/video.mp4"


@pytest.mark.asyncio
async def test_init_video_post_file_upload(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"publish_id": "publish-456", "upload_url": "https://up.tiktok/123"}})
    )
    with _patch_client(fake):
        result = await client.init_video_post(
            source="FILE_UPLOAD",
            title="Upload me",
            privacy_level="SELF_ONLY",
            video_size=1024,
        )

    assert result["data"]["upload_url"] == "https://up.tiktok/123"
    payload = fake.calls[0]["json"]
    assert payload["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": 1024,
        "chunk_size": 1024,
        "total_chunk_count": 1,
    }
    assert payload["post_info"]["privacy_level"] == "SELF_ONLY"


@pytest.mark.asyncio
async def test_init_video_upload_pull_from_url(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"publish_id": "upload-123"}}))
    with _patch_client(fake):
        result = await client.init_video_upload(
            source="PULL_FROM_URL",
            video_url="https://verified.example/video.mp4",
        )

    assert result["data"]["publish_id"] == "upload-123"
    assert fake.calls[0]["url"] == (
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
    )
    assert fake.calls[0]["json"] == {
        "source_info": {
            "source": "PULL_FROM_URL",
            "video_url": "https://verified.example/video.mp4",
        }
    }


@pytest.mark.asyncio
async def test_init_video_upload_file_upload(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {"data": {"publish_id": "upload-456", "upload_url": "https://open-upload.tiktokapis.com/video/"}},
        )
    )
    with _patch_client(fake):
        await client.init_video_upload(source="FILE_UPLOAD", video_size=1024)

    assert fake.calls[0]["json"]["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": 1024,
        "chunk_size": 1024,
        "total_chunk_count": 1,
    }


@pytest.mark.asyncio
async def test_upload_video_file_sets_range_headers(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with _patch_client(fake):
        await client.upload_video_file(
            "https://open-upload.tiktokapis.com/video/?upload_id=123",
            b"video",
        )

    assert fake.calls[0]["headers"] == {
        "Content-Length": "5",
        "Content-Range": "bytes 0-4/5",
        "Content-Type": "video/mp4",
    }
    assert fake.calls[0]["content"] == b"video"


@pytest.mark.asyncio
async def test_upload_video_file_rejects_non_tiktok_host(client):
    with pytest.raises(ValueError, match="tiktokapis.com"):
        await client.upload_video_file("https://example.com/upload", b"video")


@pytest.mark.asyncio
async def test_upload_video_file_accepts_regional_tiktok_host(client):
    """Regional upload hosts like open-upload-i18n.tiktokapis.com should be accepted."""
    fake = _FakeAsyncClient(_FakeResponse(201, {}))
    with _patch_client(fake):
        await client.upload_video_file(
            "https://open-upload-i18n.tiktokapis.com/upload?upload_id=123",
            b"video",
        )
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_init_video_post_invalid_source(client):
    with pytest.raises(ValueError, match="source must be 'PULL_FROM_URL' or 'FILE_UPLOAD'"):
        await client.init_video_post(source="BAD_SOURCE")


@pytest.mark.asyncio
async def test_init_photo_post_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"publish_id": "publish-789"}})
    )
    with _patch_client(fake):
        result = await client.init_photo_post(
            photo_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
            title="My photos",
            privacy_level="PUBLIC",
        )

    assert result["data"]["publish_id"] == "publish-789"
    assert fake.calls[0]["url"] == (
        "https://open.tiktokapis.com/v2/post/publish/content/init/"
    )
    payload = fake.calls[0]["json"]
    assert payload["source_info"]["source"] == "PULL_FROM_URL"
    assert payload["source_info"]["photo_images"] == [
        "https://example.com/1.jpg",
        "https://example.com/2.jpg",
    ]
    assert payload["post_mode"] == "DIRECT_POST"
    assert payload["media_type"] == "PHOTO"


@pytest.mark.asyncio
async def test_init_photo_post_requires_at_least_one_url(client):
    with pytest.raises(ValueError, match="At least one photo URL is required"):
        await client.init_photo_post([])


@pytest.mark.asyncio
async def test_init_photo_post_limits_photo_count(client):
    with pytest.raises(ValueError, match="cannot have more than 35 photos"):
        await client.init_photo_post(["https://example.com/photo.jpg"] * 36)


@pytest.mark.asyncio
async def test_check_publish_status_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"status": "PUBLISHED"}})
    )
    with _patch_client(fake):
        result = await client.check_publish_status("publish-123")

    assert result["data"]["status"] == "PUBLISHED"
    assert fake.calls[0]["url"] == (
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    )
    assert fake.calls[0]["json"] == {"publish_id": "publish-123"}


@pytest.mark.asyncio
async def test_check_publish_status_5xx_maps_to_502(client):
    fake = _FakeAsyncClient(_FakeResponse(500, "internal server error"))
    with _patch_client(fake):
        with pytest.raises(api.TikTokAPIError) as exc_info:
            await client.check_publish_status("publish-123")

    assert exc_info.value.status_code == 502
    assert "internal server error" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_check_publish_status_invalid_publish_id(client):
    with pytest.raises(ValueError, match="publish_id"):
        await client.check_publish_status("   ")


# ── dry_run mode tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_video_post_dry_run_sets_flag(client):
    """dry_run=True should add post_info.dry_run=True to the payload."""
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"publish_id": "dry-run-123"}})
    )
    with _patch_client(fake):
        result = await client.init_video_post(
            source="PULL_FROM_URL",
            video_url="https://example.com/video.mp4",
            title="Test",
            privacy_level="PUBLIC",
            dry_run=True,
        )

    assert result["data"]["publish_id"] == "dry-run-123"
    payload = fake.calls[0]["json"]
    assert payload["post_info"]["dry_run"] is True


@pytest.mark.asyncio
async def test_init_video_post_no_dry_run_omits_flag(client):
    """dry_run=False (default) should not add the dry_run flag."""
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"data": {"publish_id": "real-123"}})
    )
    with _patch_client(fake):
        await client.init_video_post(
            source="PULL_FROM_URL",
            video_url="https://example.com/video.mp4",
            title="Test",
        )

    payload = fake.calls[0]["json"]
    assert "dry_run" not in payload["post_info"]


# ── Display API: list_videos & query_video tests ─────────────────────────────


@pytest.mark.asyncio
async def test_list_videos_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": {
                "videos": [
                    {"id": "v1", "title": "First", "view_count": 100, "like_count": 10},
                    {"id": "v2", "title": "Second", "view_count": 200, "like_count": 20},
                ],
                "cursor": 2,
                "has_more": True,
            }
        })
    )
    with _patch_client(fake):
        result = await client.list_videos(cursor=0, max_count=20)

    videos = result["data"]["videos"]
    assert len(videos) == 2
    assert videos[0]["id"] == "v1"
    assert videos[1]["view_count"] == 200
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"] == "https://open.tiktokapis.com/v2/video/list/"
    assert fake.calls[0]["json"]["cursor"] == 0
    assert fake.calls[0]["json"]["max_count"] == 20


@pytest.mark.asyncio
async def test_list_videos_invalid_max_count(client):
    with pytest.raises(ValueError, match="max_count must be between 1 and 100"):
        await client.list_videos(max_count=0)
    with pytest.raises(ValueError, match="max_count must be between 1 and 100"):
        await client.list_videos(max_count=101)


@pytest.mark.asyncio
async def test_query_video_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {
            "data": {
                "videos": [
                    {"id": "v1", "view_count": 500, "like_count": 50},
                ]
            }
        })
    )
    with _patch_client(fake):
        result = await client.query_video(video_ids=["v1"])

    videos = result["data"]["videos"]
    assert len(videos) == 1
    assert videos[0]["view_count"] == 500
    assert fake.calls[0]["url"] == "https://open.tiktokapis.com/v2/video/query/"
    assert fake.calls[0]["json"]["video_ids"] == ["v1"]


@pytest.mark.asyncio
async def test_query_video_empty_ids(client):
    with pytest.raises(ValueError, match="At least one video_id is required"):
        await client.query_video(video_ids=[])


@pytest.mark.asyncio
async def test_query_video_too_many_ids(client):
    with pytest.raises(ValueError, match="Cannot query more than 100"):
        await client.query_video(video_ids=["v"] * 101)
