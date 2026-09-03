"""Unit tests for the platform publishing pipeline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    async def get(self, url, headers=None, params=None):
        self.calls.append({
            "method": "GET",
            "url": url,
            "headers": headers,
            "params": params,
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
        result = await pub._publish_threads("tok-123", "Hello image!", account, post, ["/tmp/img.png"], ["fake/img.png"])

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


@pytest.mark.asyncio
async def test_publish_threads_video(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path: "https://cdn.example.com/vid.mp4")
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "11111"}),
        _FakeResponse(200, {"id": "22222"}),
    ])

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Watch this!", account, post, ["/tmp/vid.mp4"], ["fake/vid.mp4"])

    assert result.success is True
    assert result.platform_post_id == "22222"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["video_url"] == "https://cdn.example.com/vid.mp4"


@pytest.mark.asyncio
async def test_publish_threads_carousel(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path: f"https://cdn.example.com/{path}")
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "11111"}),
        _FakeResponse(200, {"id": "22222"}),
        _FakeResponse(200, {"id": "33333"}),
        _FakeResponse(200, {"id": "44444"}),
    ])

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads(
            "tok-123", "Carousel post!", account, post,
            ["/tmp/slide1.png", "/tmp/slide2.png"],
            ["fake/slide1.png", "fake/slide2.png"],
        )

    assert result.success is True
    assert result.platform_post_id == "44444"
    # First two calls create carousel items, third creates the carousel container, fourth publishes
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["is_carousel_item"] == "true"
    assert fake.calls[1]["data"]["media_type"] == "IMAGE"
    assert fake.calls[1]["data"]["is_carousel_item"] == "true"
    assert fake.calls[2]["data"]["media_type"] == "CAROUSEL"
    assert fake.calls[2]["data"]["children"] == "11111,22222"
    assert fake.calls[2]["data"]["text"] == "Carousel post!"
    assert fake.calls[3]["data"]["creation_id"] == "33333"


@pytest.mark.asyncio
async def test_publish_twitter_thread(account, post):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"data": {"id": "1111111111"}}),
        _FakeResponse(200, {"data": {"id": "2222222222"}}),
    ])
    text = " ".join(["hello"] * 60)
    first_tweet = " ".join(["hello"] * 46)

    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_twitter("tok-123", text, account, post, [])

    assert result.success is True
    assert result.platform_post_id == "1111111111"
    assert result.platform_url == "https://twitter.com/testuser/status/1111111111"
    assert len(fake.calls) == 2
    assert fake.calls[0]["json"]["text"] == first_tweet
    assert fake.calls[1]["json"]["reply"]["in_reply_to_tweet_id"] == "1111111111"


@pytest.mark.asyncio
async def test_publish_twitter_quota_exceeded(account, post):
    fake = _FakeAsyncClient(_FakeResponse(402, {"status": 402, "detail": "Quota"}))

    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_twitter("tok-123", "Hello!", account, post, [])

    assert result.success is False
    assert "monthly write quota exhausted" in result.error


@pytest.mark.asyncio
async def test_publish_tiktok_defaults_to_upload_draft(monkeypatch):
    client = SimpleNamespace(
        init_video_upload=AsyncMock(return_value={"data": {"publish_id": "draft-123"}}),
        init_video_post=AsyncMock(),
        check_publish_status=AsyncMock(
            return_value={"data": {"status": "SEND_TO_USER_INBOX"}}
        ),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator")
    post = SimpleNamespace(platform_specific={})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "draft-123"
    client.init_video_upload.assert_awaited_once_with(
        source="PULL_FROM_URL",
        video_url="https://verified.example/video.mp4",
    )
    client.init_video_post.assert_not_awaited()
    client.check_publish_status.assert_awaited_once_with("draft-123")


@pytest.mark.asyncio
async def test_publish_tiktok_supports_direct_post(monkeypatch):
    client = SimpleNamespace(
        get_creator_info=AsyncMock(
            return_value={"data": {"privacy_level_options": ["SELF_ONLY"]}}
        ),
        init_video_upload=AsyncMock(),
        init_video_post=AsyncMock(return_value={"data": {"publish_id": "direct-123"}}),
        check_publish_status=AsyncMock(
            return_value={
                "data": {
                    "status": "PUBLISH_COMPLETE",
                    "publicaly_available_post_id": ["video-123"],
                }
            }
        ),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator")
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "DIRECT_POST"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_url == "https://www.tiktok.com/@creator/video/video-123"
    client.init_video_post.assert_awaited_once()
    client.init_video_upload.assert_not_awaited()


# ── Instagram sidecar publishing tests ──────────────────────────────────────


@pytest.fixture
def ig_account_with_session():
    return SimpleNamespace(
        account_id="17841463022505300",
        username="cloudless.gr",
        platform="instagram",
        meta_data={"private_api_session_id": "sid-abc123"},
    )


@pytest.fixture
def ig_account_no_session():
    return SimpleNamespace(
        account_id="17841463022505300",
        username="cloudless.gr",
        platform="instagram",
        meta_data={},
    )


@pytest.fixture
def ig_post():
    return SimpleNamespace(platform_specific={})


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_photo(ig_account_with_session, ig_post, tmp_path):
    """Sidecar path: single photo upload via private API."""
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "123456", "pk": 123456, "code": "Cabc123"}))

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token", "Nice photo!", ig_account_with_session, ig_post,
            [str(img)], ["uploads/2024/01/img.jpg"],
        )

    assert result.success is True
    assert result.platform_post_id == "123456"
    assert result.platform_url == "https://www.instagram.com/p/Cabc123/"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/photo/upload")
    assert fake.calls[0]["data"]["caption"] == "Nice photo!"
    assert "file" in fake.calls[0]["files"]


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_video(ig_account_with_session, ig_post, tmp_path):
    """Sidecar path: single video upload via private API."""
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "vid-789", "pk": 789, "code": "Cvid456"}))

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token", "Video caption", ig_account_with_session, ig_post,
            [str(vid)], ["uploads/2024/01/clip.mp4"],
        )

    assert result.success is True
    assert result.platform_post_id == "vid-789"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/video/upload")
    assert "file" in fake.calls[0]["files"]


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_album(ig_account_with_session, ig_post, tmp_path):
    """Sidecar path: carousel/album upload via private API."""
    paths = []
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xe0")
        paths.append(str(p))
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "album-111", "pk": 111, "code": "Calb222"}))

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token", "Carousel!", ig_account_with_session, ig_post,
            paths, ["uploads/a.jpg", "uploads/b.jpg", "uploads/c.jpg"],
        )

    assert result.success is True
    assert result.platform_post_id == "album-111"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/album/upload")
    assert "files" in fake.calls[0]["files"]


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_no_session_falls_back_to_graph(ig_account_no_session, ig_post, monkeypatch):
    """When no sidecar session exists, fall back to Graph API."""
    # Make the account look like a business account to skip the probe
    ig_account_no_session.meta_data = {"account_type": "business", "ig_business_id": "17841463022505300"}
    monkeypatch.setattr(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17841460000000001"}),
        _FakeResponse(200, {"id": "17841460000000002"}),
    ])

    with patch("app.services.instagram_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_instagram(
            "graph-token", "Fallback!", ig_account_no_session, ig_post,
            ["/tmp/img.png"], ["fake/img.png"],
        )

    assert result.success is True
    assert result.platform_post_id == "17841460000000002"
    # Should have called Graph API (2 calls: container + publish)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_no_media(ig_account_with_session, ig_post):
    """Sidecar path: no media → error (Instagram requires at least one image/video)."""
    result = await pub._publish_instagram(
        "graph-token", "Text only", ig_account_with_session, ig_post, [], [],
    )

    assert result.success is False
    assert "at least one image or video" in result.error


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_session_expired(ig_account_with_session, ig_post, tmp_path):
    """Sidecar returns login_required → falls back to Graph API."""
    # Make the account look like a business account to skip the Graph probe
    ig_account_with_session.meta_data = {
        "private_api_session_id": "sid-abc123",
        "account_type": "business",
        "ig_business_id": "17841463022505300",
    }
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake_sidecar = _FakeAsyncClient(_FakeResponse(401, {"detail": "login_required"}))

    # Graph API mock for fallback (numeric IDs required by _validate_id)
    fake_graph = _FakeAsyncClient([
        _FakeResponse(200, {"id": "17841460000000003"}),
        _FakeResponse(200, {"id": "17841460000000004"}),
    ])

    import app.services.instagram_api as graph_mod
    import app.services.instagram_private_api as priv_mod

    with patch.object(priv_mod, "httpx") as mock_priv_httpx, \
         patch.object(graph_mod, "httpx") as mock_graph_httpx, \
         patch.object(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png"):

        mock_priv_httpx.AsyncClient = lambda timeout=60.0: fake_sidecar
        mock_graph_httpx.AsyncClient = lambda timeout=30.0: fake_graph

        result = await pub._publish_instagram(
            "graph-token", "After expiry", ig_account_with_session, ig_post,
            [str(img)], ["uploads/img.jpg"],
        )

    assert result.success is True
    assert result.platform_post_id == "17841460000000004"


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_error_no_fallback(ig_account_with_session, ig_post, tmp_path):
    """Sidecar fails with non-session error and Graph API also fails → return sidecar error."""
    # Make the account look like a business account to skip the Graph probe
    ig_account_with_session.meta_data = {
        "private_api_session_id": "sid-abc123",
        "account_type": "business",
        "ig_business_id": "17841463022505300",
    }
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake_sidecar = _FakeAsyncClient(_FakeResponse(400, {"detail": "Invalid media format"}))
    fake_graph = _FakeAsyncClient(_FakeResponse(400, {"error": {"message": "Graph also failed"}}))

    import app.services.instagram_api as graph_mod
    import app.services.instagram_private_api as priv_mod

    with patch.object(priv_mod, "httpx") as mock_priv_httpx, \
         patch.object(graph_mod, "httpx") as mock_graph_httpx, \
         patch.object(pub, "_media_public_url", lambda path: "https://cdn.example.com/img.png"):

        mock_priv_httpx.AsyncClient = lambda timeout=60.0: fake_sidecar
        mock_graph_httpx.AsyncClient = lambda timeout=30.0: fake_graph

        result = await pub._publish_instagram(
            "graph-token", "Bad media", ig_account_with_session, ig_post,
            [str(img)], ["uploads/img.jpg"],
        )

    assert result.success is False
    # Should return the sidecar error (more actionable)
    assert "Invalid media format" in result.error


def test_sidecar_file_path_mapping():
    """Test the host-to-sidecar path mapping."""
    assert pub._sidecar_file_path("/app/uploads/2024/01/img.jpg") == "/uploads/2024/01/img.jpg"
    assert pub._sidecar_file_path("uploads/2024/01/img.jpg") == "/uploads/2024/01/img.jpg"
    assert pub._sidecar_file_path("/uploads/2024/01/img.jpg") == "/uploads/2024/01/img.jpg"
    assert pub._sidecar_file_path("") is None
