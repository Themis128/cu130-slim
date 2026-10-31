"""Unit tests for the platform publishing pipeline."""

import ast as _ast
import json as _json
import pathlib as _pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import publishing as pub
from app.services import x_web


@pytest.fixture(autouse=True)
def _isolate_x_web_fallback(monkeypatch):
    """Keep browser-fallback tests off live X_WEB_FALLBACK_ENABLED config.

    When the real x_web fallback is configured, _publish_twitter_fallbacks
    consults the live Redis ``x_web:guard`` rate limiter before reaching the
    mocked browser bridge — making results depend on real publish history.
    """
    monkeypatch.setattr(x_web, "is_configured", lambda: False)


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

    async def post(self, url, headers=None, params=None, data=None, json=None, content=None, files=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "params": params,
                "data": data,
                "json": json,
                "content": content,
                "files": files,
            }
        )
        return self._next_response()

    async def get(self, url, headers=None, params=None):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "headers": headers,
                "params": params,
            }
        )
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
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "12345"}),
            _FakeResponse(200, {"status_code": "FINISHED"}),
            _FakeResponse(200, {"id": "67890"}),
        ]
    )

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello Threads!", account, post, [])

    assert result.success is True
    assert result.platform_post_id == "67890"
    assert result.platform_url == "https://www.threads.net/@testuser/post/67890"
    assert len(fake.calls) == 3
    assert fake.calls[0]["data"]["media_type"] == "TEXT"
    assert fake.calls[0]["data"]["text"] == "Hello Threads!"
    assert fake.calls[2]["data"]["creation_id"] == "12345"


@pytest.mark.asyncio
async def test_publish_threads_image(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "45678"}),
            _FakeResponse(200, {"status_code": "FINISHED"}),
            _FakeResponse(200, {"id": "78901"}),
        ]
    )

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello image!", account, post, ["/tmp/img.png"], ["fake/img.png"])

    assert result.success is True
    assert result.platform_post_id == "78901"
    assert result.platform_url == "https://www.threads.net/@testuser/post/78901"
    assert len(fake.calls) == 3
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["image_url"] == "https://cdn.example.com/img.png"
    assert fake.calls[2]["data"]["creation_id"] == "45678"


@pytest.mark.asyncio
async def test_publish_threads_access_denied(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(_FakeResponse(403, {"error": {"message": "Access denied"}}))

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Hello!", account, post, ["/tmp/img.png"], ["fake/img.png"])

    assert result.success is False
    assert "Threads API access denied" in result.error


@pytest.mark.asyncio
async def test_publish_threads_video(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/vid.mp4")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "11111"}),
            _FakeResponse(200, {"status_code": "FINISHED"}),
            _FakeResponse(200, {"id": "22222"}),
        ]
    )

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok-123", "Watch this!", account, post, ["/tmp/vid.mp4"], ["fake/vid.mp4"])

    assert result.success is True
    assert result.platform_post_id == "22222"
    assert fake.calls[0]["data"]["media_type"] == "VIDEO"
    assert fake.calls[0]["data"]["video_url"] == "https://cdn.example.com/vid.mp4"


@pytest.mark.asyncio
async def test_publish_threads_carousel(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: f"https://cdn.example.com/{path}")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "11111"}),
            _FakeResponse(200, {"id": "22222"}),
            _FakeResponse(200, {"id": "33333"}),
            _FakeResponse(200, {"status_code": "FINISHED"}),
            _FakeResponse(200, {"id": "44444"}),
        ]
    )

    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads(
            "tok-123",
            "Carousel post!",
            account,
            post,
            ["/tmp/slide1.png", "/tmp/slide2.png"],
            ["fake/slide1.png", "fake/slide2.png"],
        )

    assert result.success is True
    assert result.platform_post_id == "44444"
    # First two calls create carousel items, third creates the carousel
    # container, fourth polls status, fifth publishes
    assert fake.calls[0]["data"]["media_type"] == "IMAGE"
    assert fake.calls[0]["data"]["is_carousel_item"] == "true"
    assert fake.calls[1]["data"]["media_type"] == "IMAGE"
    assert fake.calls[1]["data"]["is_carousel_item"] == "true"
    assert fake.calls[2]["data"]["media_type"] == "CAROUSEL"
    assert fake.calls[2]["data"]["children"] == "11111,22222"
    assert fake.calls[2]["data"]["text"] == "Carousel post!"
    assert fake.calls[4]["data"]["creation_id"] == "33333"


@pytest.mark.asyncio
async def test_publish_to_platform_whatsapp_is_soft_skipped():
    """WhatsApp should not hard-fail the multi-platform publishing pipeline."""
    account = SimpleNamespace(platform="whatsapp", access_token_enc=b"unused")
    post = SimpleNamespace(content_text="")
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is False
    assert result.skipped is True
    assert "whatsapp" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_to_platform_telegram_is_not_a_messaging_skip():
    """Telegram publishes to the managed channel — it must not hit the
    whatsapp/viber messaging-channel skip (whatever later guard fires)."""
    account = SimpleNamespace(platform="telegram", access_token_enc=b"unused")
    post = SimpleNamespace(content_text="")
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is False
    assert "messaging channel" not in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_to_platform_rejects_empty_copy(monkeypatch):
    """A post with no text, override, hashtags or link must fail loudly —
    two Instagram posts previously shipped with completely empty captions."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(return_value=None))
    account = SimpleNamespace(platform="instagram", access_token_enc=b"enc")
    post = SimpleNamespace(
        content_text="",
        platform_specific={},
        hashtags=[],
        link_url=None,
        media_ids=["m1"],
    )
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is False
    assert "no text" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_to_platform_gibberish_is_skipped(monkeypatch):
    """Mangled LLM copy must never reach a platform — the "cloudless.g GGr
    clientsget" LinkedIn post shipped because generation bypassed the
    quality pipeline."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(
        pub,
        "_build_post_text",
        lambda post, platform: "Speed + security so cloudless.g GGr clientsget",
    )
    monkeypatch.setattr(pub, "detect_gibberish", AsyncMock(return_value=["GGr", "clientsget"]))
    account = SimpleNamespace(platform="linkedin", access_token_enc=b"enc")
    post = SimpleNamespace(content_text="")
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is False
    assert result.skipped is True
    assert "mangled" in (result.error or "").lower()
    assert "GGr" in (result.error or "")


@pytest.mark.asyncio
async def test_publish_to_platform_gibberish_detector_failure_allows_flow(
    monkeypatch,
):
    """A gibberish-detector exception must be advisory, never a publish
    blocker — the flow continues to the duplicate/media checks."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(pub, "_build_post_text", lambda post, platform: "Perfectly good copy.")
    monkeypatch.setattr(pub, "detect_gibberish", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(pub, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_resolve_media_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_resolve_media_storage_paths", AsyncMock(return_value=[]))
    account = SimpleNamespace(platform="linkedin", access_token_enc=b"enc")
    post = SimpleNamespace(content_text="", media_ids=[])
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    # LinkedIn requires media → the flow should reach the media pre-flight
    # (not die on the detector) and skip for missing media instead.
    assert "mangled" not in (result.error or "").lower()
    assert "media" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_twitter_thread(account, post):
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": {"id": "1111111111"}}),
            _FakeResponse(200, {"data": {"id": "2222222222"}}),
        ]
    )
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
async def test_publish_twitter_quota_exceeded(account, post, monkeypatch):
    """402 credits-depleted falls back to browser; DEFERS if browser also fails."""
    fake = _FakeAsyncClient(_FakeResponse(402, {"status": 402, "detail": "credits-depleted"}))
    # Transient browser failures come back as skipped quota results.
    fallback = AsyncMock(return_value=pub.PublishResult(success=False, skipped=True, error="browser down"))
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", fallback)

    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_twitter("tok-123", "Hello!", account, post, [])

    assert result.success is False
    assert result.skipped is False  # never complete the queue row on capacity errors
    assert result.permanent is False
    assert result.retry_after is not None
    assert "quota" in (result.error or "").lower() or "credits" in (result.error or "").lower()
    assert "Reconnect will not fix" in (result.error or "")
    fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_twitter_quota_browser_fallback_succeeds(account, post, monkeypatch):
    fake = _FakeAsyncClient(_FakeResponse(402, {"status": 402, "detail": "Quota"}))
    fallback = AsyncMock(
        return_value=pub.PublishResult(
            success=True,
            platform_post_id="999",
            platform_url="https://x.com/u/status/999",
        )
    )
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", fallback)

    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_twitter("tok-123", "Hello!", account, post, [])

    assert result.success is True
    assert result.platform_post_id == "999"


@pytest.mark.asyncio
async def test_publish_twitter_identity_mismatch_propagates(account, post, monkeypatch):
    """A hard browser failure (wrong-account session) must NOT become a
    quota skip — it has to surface so the worker retries and alerts."""
    fake = _FakeAsyncClient(_FakeResponse(402, {"status": 402, "detail": "credits-depleted"}))
    fallback = AsyncMock(
        return_value=pub.PublishResult(
            success=False,
            error="X browser session is logged in as @other, expected @them — re-login",
        )
    )
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", fallback)

    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_twitter("tok-123", "Hello!", account, post, [])

    assert result.success is False
    assert result.skipped is False
    assert "logged in as @other" in (result.error or "")


def test_fit_x_limit_counts_weighted_chars():
    """URLs count 23 (t.co); ellipsis/emoji count double."""
    text = "a" * 278 + "…"  # 278*1 + 2 = 280 weighted, 279 raw
    assert pub._x_weighted_len(text) == 280
    assert pub._fit_x_limit(text) == text

    over = "word " * 100  # 500 raw
    trimmed = pub._fit_x_limit(over)
    assert pub._x_weighted_len(trimmed) <= 280
    assert trimmed.endswith("word")

    linked = "check this https://cloudless.gr " + "x" * 300
    trimmed = pub._fit_x_limit(linked)
    assert pub._x_weighted_len(trimmed) <= 280


@pytest.mark.asyncio
async def test_publish_tiktok_defaults_to_direct_post(monkeypatch):
    """Post-audit default: DIRECT_POST + PUBLIC_TO_EVERYONE."""
    client = SimpleNamespace(
        get_creator_info=AsyncMock(
            return_value={
                "data": {
                    "privacy_level_options": [
                        "PUBLIC_TO_EVERYONE",
                        "MUTUAL_FOLLOW_FRIENDS",
                        "SELF_ONLY",
                    ]
                }
            }
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
    monkeypatch.setattr(pub, "_media_public_url", lambda _path, **kwargs: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "video-123"
    client.init_video_post.assert_awaited_once()
    kwargs = client.init_video_post.await_args.kwargs
    assert kwargs["privacy_level"] == "PUBLIC_TO_EVERYONE"
    client.init_video_upload.assert_not_awaited()
    client.check_publish_status.assert_awaited_once_with("direct-123")


@pytest.mark.asyncio
async def test_publish_tiktok_direct_post_falls_back_to_media_upload(monkeypatch):
    """A lingering unaudited flag retries the init as an inbox draft."""
    from app.services.tiktok_api import TikTokAPIError

    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}}),
        init_video_post=AsyncMock(
            side_effect=TikTokAPIError(
                403,
                '{"error":{"code":"unaudited_client_can_only_post_to_private_accounts"}}',
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
            )
        ),
        init_video_upload=AsyncMock(return_value={"data": {"publish_id": "draft-fallback"}}),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _path, **kwargs: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "draft-fallback"
    client.init_video_post.assert_awaited_once()
    client.init_video_upload.assert_awaited_once_with(
        source="PULL_FROM_URL",
        video_url="https://verified.example/video.mp4",
    )


@pytest.mark.asyncio
async def test_publish_tiktok_media_upload_override_still_works(monkeypatch):
    """Explicit per-post publish_mode=MEDIA_UPLOAD keeps the inbox path."""
    client = SimpleNamespace(
        init_video_upload=AsyncMock(return_value={"data": {"publish_id": "draft-123"}}),
        init_video_post=AsyncMock(),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _path, **kwargs: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "MEDIA_UPLOAD"}})

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
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}}),
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
    monkeypatch.setattr(pub, "_media_public_url", lambda _path, **kwargs: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "DIRECT_POST"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "video-123"
    assert result.platform_url == "https://www.tiktok.com/@creator/video/video-123"
    assert result.platform_meta["tiktok"]["publish_id"] == "direct-123"
    assert result.platform_meta["tiktok"]["publicaly_available_post_id"] == "video-123"
    client.init_video_post.assert_awaited_once()
    client.init_video_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_tiktok_file_upload_uses_local_video(monkeypatch, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"\x00" * 2048)
    client = SimpleNamespace(
        init_video_upload=AsyncMock(
            return_value={
                "data": {
                    "publish_id": "v_inbox_file~v2.1",
                    "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1",
                }
            }
        ),
        init_video_post=AsyncMock(),
        upload_video_file=AsyncMock(),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "app.services.tiktok_api.validate_tiktok_video_constraints",
        lambda _path: None,
    )
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "MEDIA_UPLOAD"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, [str(video_path)], ["uploads/clip.mp4"])

    assert result.success is True
    assert result.platform_post_id == "v_inbox_file~v2.1"
    assert result.platform_meta["tiktok"]["publish_id"] == "v_inbox_file~v2.1"
    client.init_video_upload.assert_awaited_once()
    kwargs = client.init_video_upload.await_args.kwargs
    assert kwargs["source"] == "FILE_UPLOAD"
    assert kwargs["video_size"] == 2048
    assert kwargs["chunk_size"] == 2048
    client.upload_video_file.assert_awaited_once()
    client.init_video_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_tiktok_rejects_low_fps_local_video(monkeypatch, tmp_path):
    video_path = tmp_path / "slow.mp4"
    video_path.write_bytes(b"\x00" * 1024)
    client = SimpleNamespace(
        init_video_upload=AsyncMock(),
        init_video_post=AsyncMock(),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(
        "app.services.tiktok_api.validate_tiktok_video_constraints",
        lambda _path: "TikTok media validation failed: 15.00 FPS (TikTok requires ≥23 FPS)",
    )
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "MEDIA_UPLOAD"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, [str(video_path)], ["uploads/slow.mp4"])

    assert result.success is False
    assert "23 FPS" in (result.error or "")
    client.init_video_upload.assert_not_awaited()
    client.init_video_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_tiktok_resumes_existing_publish_id(monkeypatch):
    client = SimpleNamespace(
        init_video_upload=AsyncMock(),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(
        platform_specific={
            "tiktok": {
                "publish_mode": "MEDIA_UPLOAD",
                "publish_id": "v_inbox_file~v2.resume",
            }
        }
    )

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "v_inbox_file~v2.resume"
    client.init_video_upload.assert_not_awaited()
    client.check_publish_status.assert_awaited_once_with("v_inbox_file~v2.resume")


@pytest.mark.asyncio
async def test_publish_tiktok_modeless_inbox_resume_infers_upload(monkeypatch):
    """A stored v_inbox_* publish_id with no explicit mode resumes as
    MEDIA_UPLOAD — SEND_TO_USER_INBOX is terminal there, not under the
    new DIRECT_POST default."""
    client = SimpleNamespace(
        init_video_upload=AsyncMock(),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_id": "v_inbox_file~v2.abc"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is True
    assert result.platform_post_id == "v_inbox_file~v2.abc"
    client.check_publish_status.assert_awaited_once_with("v_inbox_file~v2.abc")


@pytest.mark.asyncio
async def test_publish_tiktok_surfaces_fail_reason(monkeypatch):
    client = SimpleNamespace(
        init_video_upload=AsyncMock(return_value={"data": {"publish_id": "draft-fail"}}),
        check_publish_status=AsyncMock(return_value={"data": {"status": "FAILED", "fail_reason": "internal_error"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _path, **kwargs: "https://verified.example/video.mp4")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "MEDIA_UPLOAD"}})

    result = await pub._publish_tiktok("token", "Caption", account, post, ["video.mp4"], ["fake/video.mp4"])

    assert result.success is False
    assert result.platform_post_id == "draft-fail"
    assert "internal_error" in (result.error or "")


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
    # 1st response: pre-publish dedup check (recent media list — empty)
    # 2nd response: login_by_sessionid (best-effort session restore)
    # 3rd response: photo upload
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),
            _FakeResponse(200, {"ok": True}),
            _FakeResponse(200, {"id": "123456", "pk": 123456, "code": "Cabc123"}),
        ]
    )

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token",
            "Nice photo!",
            ig_account_with_session,
            ig_post,
            [str(img)],
            ["uploads/2024/01/img.jpg"],
        )

    assert result.success is True
    assert result.platform_post_id == "123456"
    assert result.platform_url == "https://www.instagram.com/p/Cabc123/"
    assert len(fake.calls) == 3
    assert fake.calls[1]["url"].endswith("/auth/login/by/sessionid")
    assert fake.calls[2]["url"].endswith("/photo/upload")
    assert fake.calls[2]["data"]["caption"] == "Nice photo!"
    assert "file" in fake.calls[2]["files"]


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_video(ig_account_with_session, ig_post, tmp_path):
    """Sidecar path: single video upload via private API."""
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    # 1st response: pre-publish dedup check (recent media list — empty)
    # 2nd response: login_by_sessionid (best-effort session restore)
    # 3rd response: video upload
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),
            _FakeResponse(200, {"ok": True}),
            _FakeResponse(200, {"id": "vid-789", "pk": 789, "code": "Cvid456"}),
        ]
    )

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token",
            "Video caption",
            ig_account_with_session,
            ig_post,
            [str(vid)],
            ["uploads/2024/01/clip.mp4"],
        )

    assert result.success is True
    assert result.platform_post_id == "vid-789"
    assert len(fake.calls) == 3
    assert fake.calls[1]["url"].endswith("/auth/login/by/sessionid")
    assert fake.calls[2]["url"].endswith("/video/upload")
    assert "file" in fake.calls[2]["files"]


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_album(ig_account_with_session, ig_post, tmp_path):
    """Sidecar path: carousel/album upload via private API."""
    paths = []
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xe0")
        paths.append(str(p))
    # 1st response: pre-publish dedup check (recent media list — empty)
    # 2nd response: login_by_sessionid (best-effort session restore)
    # 3rd response: album upload
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),
            _FakeResponse(200, {"ok": True}),
            _FakeResponse(200, {"id": "album-111", "pk": 111, "code": "Calb222"}),
        ]
    )

    with patch("app.services.instagram_private_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_instagram(
            "graph-token",
            "Carousel!",
            ig_account_with_session,
            ig_post,
            paths,
            ["uploads/a.jpg", "uploads/b.jpg", "uploads/c.jpg"],
        )

    assert result.success is True
    assert result.platform_post_id == "album-111"
    assert len(fake.calls) == 3
    assert fake.calls[1]["url"].endswith("/auth/login/by/sessionid")
    assert fake.calls[2]["url"].endswith("/album/upload")
    # files is a list of (field_name, (filename, file_obj, content_type)) tuples
    assert len(fake.calls[2]["files"]) == 3


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_no_session_falls_back_to_graph(ig_account_no_session, ig_post, monkeypatch):
    """When no sidecar session exists, fall back to Graph API."""
    # Make the account look like a business account to skip the probe
    ig_account_no_session.meta_data = {"account_type": "business", "ig_business_id": "17841463022505300"}
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),  # dedup check: recent media list (empty)
            _FakeResponse(200, {"id": "17841460000000001"}),  # create container
            _FakeResponse(200, {"status_code": "FINISHED"}),  # status poll
            _FakeResponse(200, {"id": "17841460000000002"}),  # publish
            _FakeResponse(200, {"permalink": "https://www.instagram.com/p/Cfb123/"}),  # permalink lookup
        ]
    )

    with patch("app.services.instagram_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        result = await pub._publish_instagram(
            "graph-token",
            "Fallback!",
            ig_account_no_session,
            ig_post,
            ["/tmp/img.png"],
            ["fake/img.png"],
        )

    assert result.success is True
    assert result.platform_post_id == "17841460000000002"
    assert result.platform_url == "https://www.instagram.com/p/Cfb123/"
    # 5 calls: dedup media list + container + status + publish + permalink
    assert len(fake.calls) == 5


@pytest.mark.asyncio
async def test_publish_instagram_publish_2207051_post_is_live(ig_account_no_session, ig_post, monkeypatch):
    """media_publish returns 403/2207051 but the post went live → success, no duplicate."""
    from datetime import UTC, datetime

    ig_account_no_session.meta_data = {"account_type": "business", "ig_business_id": "17841463022505300"}
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),  # dedup check
            _FakeResponse(200, {"id": "17841460000000005"}),  # create container
            _FakeResponse(200, {"status_code": "FINISHED"}),  # status poll
            _FakeResponse(
                403,
                {  # media_publish false-negative
                    "error": {
                        "code": 4,
                        "error_subcode": 2207051,
                        "message": "Application request limit reached",
                        "type": "OAuthException",
                    }
                },
            ),
            _FakeResponse(
                200,
                {
                    "data": [
                        {  # verify: post IS live
                            "id": "17841460000000009",
                            "caption": "Limit test",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "permalink": "https://www.instagram.com/p/Cok999/",
                        }
                    ]
                },
            ),
        ]
    )

    with patch("app.services.instagram_api.httpx.AsyncClient", new=lambda timeout=30.0: fake), patch("app.services.publishing.asyncio.sleep", new=AsyncMock()):
        result = await pub._publish_instagram(
            "graph-token",
            "Limit test",
            ig_account_no_session,
            ig_post,
            ["/tmp/img.png"],
            ["fake/img.png"],
        )

    assert result.success is True
    assert result.platform_post_id == "17841460000000009"
    assert result.platform_url == "https://www.instagram.com/p/Cok999/"


@pytest.mark.asyncio
async def test_publish_instagram_publish_error_post_not_live(ig_account_no_session, ig_post, monkeypatch):
    """media_publish errors and the post is NOT on the feed → real failure."""
    ig_account_no_session.meta_data = {"account_type": "business", "ig_business_id": "17841463022505300"}
    monkeypatch.setattr(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),  # dedup check
            _FakeResponse(200, {"id": "17841460000000005"}),  # create container
            _FakeResponse(200, {"status_code": "FINISHED"}),  # status poll
            _FakeResponse(403, {"error": {"code": 4, "error_subcode": 2207051, "message": "Application request limit reached"}}),
            _FakeResponse(200, {"data": []}),  # verify 1: nothing live
            _FakeResponse(200, {"data": []}),  # verify 2: nothing live
            _FakeResponse(200, {"data": []}),  # verify 3: nothing live
        ]
    )

    with patch("app.services.instagram_api.httpx.AsyncClient", new=lambda timeout=30.0: fake), patch("app.services.publishing.asyncio.sleep", new=AsyncMock()):
        result = await pub._publish_instagram(
            "graph-token",
            "Limit test",
            ig_account_no_session,
            ig_post,
            ["/tmp/img.png"],
            ["fake/img.png"],
        )

    assert result.success is False
    assert "publish failed" in (result.error or "")
    # Publish-boundary failure must be flagged ambiguous so the queue worker
    # schedules the delayed feed reconciliation (covers indexing lag minutes
    # beyond the in-path poll).
    assert result.ambiguous is True


@pytest.mark.asyncio
async def test_publish_instagram_sidecar_no_media(ig_account_with_session, ig_post):
    """Sidecar path: no media → error (Instagram requires at least one image/video)."""
    result = await pub._publish_instagram(
        "graph-token",
        "Text only",
        ig_account_with_session,
        ig_post,
        [],
        [],
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
    # 1st sidecar response: login_by_sessionid (best-effort, fails with 401)
    # 2nd sidecar response: upload → 401 login_required (session expired)
    fake_sidecar = _FakeAsyncClient(
        [
            _FakeResponse(401, {"detail": "login_required"}),
            _FakeResponse(401, {"detail": "login_required"}),
        ]
    )

    # Graph API mock for fallback (numeric IDs required by _validate_id)
    fake_graph = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": []}),  # dedup check: recent media list (empty)
            _FakeResponse(200, {"id": "17841460000000003"}),  # create container
            _FakeResponse(200, {"status_code": "FINISHED"}),  # status poll
            _FakeResponse(200, {"id": "17841460000000004"}),  # publish
        ]
    )

    import app.services.instagram_api as graph_mod
    import app.services.instagram_private_api as priv_mod

    with (
        patch.object(priv_mod, "httpx") as mock_priv_httpx,
        patch.object(graph_mod, "httpx") as mock_graph_httpx,
        patch.object(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png"),
    ):
        mock_priv_httpx.AsyncClient = lambda timeout=60.0: fake_sidecar
        mock_graph_httpx.AsyncClient = lambda timeout=30.0: fake_graph

        result = await pub._publish_instagram(
            "graph-token",
            "After expiry",
            ig_account_with_session,
            ig_post,
            [str(img)],
            ["uploads/img.jpg"],
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
    # 1st sidecar response: login_by_sessionid (best-effort, succeeds)
    # 2nd sidecar response: upload → 400 "Invalid media format" (non-session error)
    fake_sidecar = _FakeAsyncClient(
        [
            _FakeResponse(200, {"ok": True}),
            _FakeResponse(400, {"detail": "Invalid media format"}),
        ]
    )
    fake_graph = _FakeAsyncClient(_FakeResponse(400, {"error": {"message": "Graph also failed"}}))

    import app.services.instagram_api as graph_mod
    import app.services.instagram_private_api as priv_mod

    with (
        patch.object(priv_mod, "httpx") as mock_priv_httpx,
        patch.object(graph_mod, "httpx") as mock_graph_httpx,
        patch.object(pub, "_media_public_url", lambda path, **kwargs: "https://cdn.example.com/img.png"),
    ):
        mock_priv_httpx.AsyncClient = lambda timeout=60.0: fake_sidecar
        mock_graph_httpx.AsyncClient = lambda timeout=30.0: fake_graph

        result = await pub._publish_instagram(
            "graph-token",
            "Bad media",
            ig_account_with_session,
            ig_post,
            [str(img)],
            ["uploads/img.jpg"],
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


def test_media_public_url_force_jpeg(monkeypatch):
    """Instagram Graph URLs must request JPEG re-encode from /media/view."""
    from app.services import publishing as pub

    monkeypatch.setattr(pub._settings, "MEDIA_PUBLIC_BASE_URL", "https://media.example")
    monkeypatch.setattr(pub._settings, "R2_PUBLIC_URL", "")
    url = pub._media_public_url("2026/01/x.webp", force_jpeg=True)
    assert url == "https://media.example/api/v1/media/view?path=2026/01/x.webp&format=jpeg"
    url2 = pub._media_public_url("2026/01/x.webp")
    assert url2 == "https://media.example/api/v1/media/view?path=2026/01/x.webp"
    assert "&format=jpeg" not in url2


@pytest.mark.asyncio
async def test_publish_facebook_group_soft_skipped(account, post):
    account.platform = "facebook"
    account.account_type = "group"
    account.meta_data = {}
    result = await pub._publish_facebook("tok", "hi", account, post, [])
    assert result.success is False
    assert result.skipped is True
    assert "Groups API is deprecated" in (result.error or "")


@pytest.mark.asyncio
async def test_publish_instagram_text_only_soft_skipped(account, post, monkeypatch):
    account.platform = "instagram"
    result = await pub._publish_instagram("tok", "caption only", account, post, [], [], None)
    assert result.success is False
    assert result.skipped is True
    assert "at least one image" in (result.error or "").lower()


def test_tiktok_clarify_url_ownership():
    msg = pub._tiktok_clarify_error("TikTok error url_ownership_unverified: bad domain")
    assert "verified" in msg.lower()
    assert "FILE_UPLOAD" in msg


def test_tiktok_clarify_unaudited():
    msg = pub._tiktok_clarify_error("unaudited_client_can_only_post_to_private_accounts")
    assert "MEDIA_UPLOAD" in msg
    assert "audit" in msg.lower()


# ── duplicate-content guard ──────────────────────────────────────────────────


class _StubScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _StubScalars(self._rows)


class _StubDB:
    """AsyncSession stand-in — execute() returns preset rows."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _StubResult(self._rows)


def _fake_post(post_id, content, media_ids=None):
    return SimpleNamespace(
        id=post_id,
        content_text=content,
        media_ids=media_ids or [],
    )


@pytest.mark.asyncio
async def test_find_duplicate_post_matches_shared_media(monkeypatch):
    import uuid

    other = _fake_post(uuid.uuid4(), "totally different caption", media_ids=[uuid.uuid4()])
    shared = other.media_ids[0]
    candidate = _fake_post(uuid.uuid4(), "fresh text", media_ids=[shared])
    monkeypatch.setattr(pub, "_build_post_text", lambda post, platform: post.content_text)

    account = SimpleNamespace(id=uuid.uuid4(), platform="linkedin")
    hit = await pub._find_duplicate_post(_StubDB([other]), candidate, account, "fresh text")
    assert hit is other


@pytest.mark.asyncio
async def test_find_duplicate_post_matches_near_identical_caption(monkeypatch):
    import uuid

    same = "We spent €100 of promo credit on a LinkedIn boost. The results beat the platform average by 9x — here's exactly what worked."
    other = _fake_post(uuid.uuid4(), same)
    candidate = _fake_post(uuid.uuid4(), "x", media_ids=[])
    monkeypatch.setattr(pub, "_build_post_text", lambda post, platform: post.content_text)

    account = SimpleNamespace(id=uuid.uuid4(), platform="linkedin")
    hit = await pub._find_duplicate_post(
        _StubDB([other]),
        candidate,
        account,
        "We spent €100 of promo credit on a LinkedIn boost. The results beat the platform average by 9x — here's what worked.",
    )
    assert hit is other


@pytest.mark.asyncio
async def test_find_duplicate_post_ignores_distinct_content(monkeypatch):
    import uuid

    other = _fake_post(uuid.uuid4(), "Notes on Postgres indexing strategies for small teams.")
    candidate = _fake_post(uuid.uuid4(), "x", media_ids=[])
    monkeypatch.setattr(pub, "_build_post_text", lambda post, platform: post.content_text)

    account = SimpleNamespace(id=uuid.uuid4(), platform="linkedin")
    hit = await pub._find_duplicate_post(
        _StubDB([other]),
        candidate,
        account,
        "New Grafana dashboard tracks container memory pressure across the cluster.",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_publish_instagram_graph_config_gaps_are_soft_skipped(account, post, monkeypatch):
    """IG graph-path config/content gaps must be skips, not retried failures.

    The 2026-10-04 digest showed IG targets burning retry attempts on
    "Instagram requires at least one image. Set an image on the post." —
    retrying cannot help when media or public-URL resolution comes up empty
    (missing MEDIA_PUBLIC_BASE_URL, text-only post).
    """
    account.meta_data = {"account_type": "business", "ig_business_id": "17841405309211844"}
    post.platform_specific = {}
    # Simulate the config gap this test targets — public-URL resolution must
    # come up empty (env MEDIA_PUBLIC_BASE_URL or /run/tunnel/url may resolve
    # URLs in the container, skipping past the guard under test).
    monkeypatch.setattr(pub, "_media_public_url", lambda *a, **kw: None)
    media_present = await pub._publish_instagram_via_graph(
        "token",
        "caption",
        account,
        post,
        ["/tmp/a.jpg"],
        ["/uploads/a.jpg"],
        None,
    )
    assert media_present.skipped is True
    assert "MEDIA_PUBLIC_BASE_URL" in (media_present.error or "")

    media_absent = await pub._publish_instagram_via_graph(
        "token",
        "caption",
        account,
        post,
        [],
        [],
        None,
    )
    assert media_absent.skipped is True
    assert "Set an image" in (media_absent.error or "")


@pytest.mark.asyncio
async def test_publish_instagram_subpath_media_gaps_are_soft_skipped(account, post):
    """Web/sidecar/instagrapi sub-paths must skip (not fail) on missing media."""
    account.meta_data = {}
    web = await pub._publish_instagram_via_web(account, "caption", post, [])
    sidecar = await pub._publish_instagram_via_sidecar(account, "caption", post, [])
    insta = await pub._publish_instagram_via_instagrapi("caption", post, [])
    for result in (web, sidecar, insta):
        assert result.skipped is True, result.error


@pytest.mark.asyncio
async def test_publish_tiktok_rejects_multi_video_post(account, post):
    """More than one video must skip with the actual rule, not a photo-branch error."""
    result = await pub._publish_tiktok(
        "token",
        "caption",
        account,
        post,
        ["/tmp/a.mp4", "/tmp/b.mp4"],
        ["/uploads/a.mp4", "/uploads/b.mp4"],
    )
    assert result.skipped is True
    assert "single video" in (result.error or "")


# ── media pre-flight safety net ──────────────────────────────────────────────


def _write_image(tmp_path, name="ok.jpg", size=(1080, 1080)):
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", size, (20, 24, 30)).save(p, "JPEG")
    return str(p)


def test_preflight_allows_valid_image(tmp_path):
    p = _write_image(tmp_path)
    assert pub.validate_media_for_platform("instagram", [p]) is None


def test_preflight_requires_media_on_instagram():
    result = pub.validate_media_for_platform("instagram", [])
    assert result is not None
    assert result.skipped is True


def test_preflight_rejects_text_only_on_linkedin():
    # Owner rule 2026-10-05: never post without media — every platform.
    result = pub.validate_media_for_platform("linkedin", [])
    assert result is not None
    assert result.skipped is True


def test_preflight_rejects_corrupt_image(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg" + b"\x00" * 20480)
    result = pub.validate_media_for_platform("instagram", [str(p)])
    assert result is not None
    assert result.skipped is True
    assert "decodable" in (result.error or "")


def test_preflight_rejects_undersized_image(tmp_path):
    p = _write_image(tmp_path, "tiny.jpg", size=(64, 64))
    result = pub.validate_media_for_platform("instagram", [p])
    assert result is not None
    assert result.skipped is True


def test_preflight_rejects_missing_file(tmp_path):
    result = pub.validate_media_for_platform("facebook", [str(tmp_path / "gone.jpg")])
    assert result is not None
    assert result.skipped is True
    assert "missing" in (result.error or "")


def test_preflight_pdf_allowed_on_linkedin_only(tmp_path):
    p = tmp_path / "deck.pdf"
    p.write_bytes(b"%PDF-1.4 minimal\n")
    assert pub.validate_media_for_platform("linkedin", [str(p)]) is None
    result = pub.validate_media_for_platform("instagram", [str(p)])
    assert result is not None
    assert result.skipped is True
    assert "PDF" in (result.error or "")


# ── pre-flight follow-ups (#302 review) ──────────────────────────────────────


def _write_sparse(tmp_path, name, size_bytes, header=b""):
    """Create a file of ``size_bytes`` without writing it all (sparse)."""
    p = tmp_path / name
    with open(p, "wb") as fh:
        fh.write(header)
        fh.truncate(size_bytes)
    return str(p)


@pytest.mark.parametrize(
    "platform,size_mb",
    [("instagram", 50), ("facebook", 50), ("twitter", 50), ("threads", 50)],
)
def test_preflight_image_byte_cap_does_not_apply_to_video(tmp_path, platform, size_mb):
    """Bug 1: the image max_bytes cap (IG 8MB / FB 10MB / X 5MB) ran before the
    video skip, so a 50MB Reel/video was soft-skipped."""
    p = _write_sparse(tmp_path, "reel.mp4", size_mb * 1024 * 1024)
    assert pub.validate_media_for_platform(platform, [p]) is None


def test_preflight_video_over_platform_video_cap_is_skipped(tmp_path):
    # IG Reels: 300MB max (Graph API IG User Media docs)
    p = _write_sparse(tmp_path, "huge.mp4", 301 * 1024 * 1024)
    result = pub.validate_media_for_platform("instagram", [p])
    assert result is not None and result.skipped is True
    assert "video limit" in (result.error or "")


def test_preflight_image_over_cap_still_skipped(tmp_path):
    p = _write_sparse(tmp_path, "big.jpg", 6 * 1024 * 1024)
    result = pub.validate_media_for_platform("twitter", [p])
    assert result is not None and result.skipped is True
    assert "image limit" in (result.error or "")


def test_preflight_rejects_video_format_instagram_does_not_accept(tmp_path):
    p = _write_sparse(tmp_path, "clip.webm", 2 * 1024 * 1024)
    result = pub.validate_media_for_platform("instagram", [p])
    assert result is not None and result.skipped is True
    assert "format" in (result.error or "")


@pytest.mark.parametrize("platform", ["facebook", "linkedin", "threads", "twitter"])
def test_preflight_skips_when_referenced_media_did_not_resolve(platform):
    """Bug 2: assets that failed to resolve were dropped → [] → text-only post."""
    result = pub.validate_media_for_platform(platform, [], expected=1)
    assert result is not None and result.skipped is True
    assert "0/1" in (result.error or "")


def test_preflight_skips_partial_media_set(tmp_path):
    p = _write_image(tmp_path)
    result = pub.validate_media_for_platform("facebook", [p], expected=2)
    assert result is not None and result.skipped is True
    assert "1/2" in (result.error or "")


def test_preflight_text_only_rejected_when_no_media_referenced():
    # Owner rule 2026-10-05: never post without media — every platform.
    result = pub.validate_media_for_platform("facebook", [], expected=0)
    assert result is not None
    assert result.skipped is True


@pytest.mark.asyncio
async def test_publish_to_platform_never_publishes_text_only_when_media_missing(monkeypatch):
    """End-to-end: FB post referencing an asset that can't be resolved must be
    skipped before dispatch, never published text-only."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_resolve_media_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_resolve_media_storage_paths", AsyncMock(return_value=["2026/10/04/a.jpg"]))
    fb = AsyncMock()
    monkeypatch.setattr(pub, "_publish_facebook", fb)
    account = SimpleNamespace(platform="facebook", access_token_enc=b"enc")
    post = SimpleNamespace(
        content_text="hello",
        platform_specific={},
        hashtags=[],
        link_url=None,
        media_ids=["m1"],
    )
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is False
    assert result.skipped is True
    assert "missing media" in (result.error or "")
    fb.assert_not_called()


@pytest.mark.parametrize("platform", ["instagram", "threads"])
def test_preflight_accepts_4x5_portrait_at_max_width(tmp_path, platform):
    """Bug 3: Meta caps the WIDTH at 1440px; height varies with the aspect
    ratio, so a 1440x1800 (4:5) portrait is valid."""
    p = _write_image(tmp_path, "portrait.jpg", size=(1440, 1800))
    assert pub.validate_media_for_platform(platform, [p]) is None


@pytest.mark.parametrize("platform", ["instagram", "threads"])
def test_preflight_rejects_width_over_1440(tmp_path, platform):
    p = _write_image(tmp_path, "wide.jpg", size=(1600, 1600))
    result = pub.validate_media_for_platform(platform, [p])
    assert result is not None and result.skipped is True
    assert "max width" in (result.error or "")


def test_preflight_instagram_landscape_191_with_short_height_ok(tmp_path):
    # ~1.9:1 at 640px wide → 336px tall; height is not min-capped by Meta
    import os as _os

    from PIL import Image

    p = str(tmp_path / "land.jpg")
    # noise so the JPEG is above MIN_IMAGE_BYTES (a flat colour compresses to ~4KB)
    Image.frombytes("RGB", (640, 336), _os.urandom(640 * 336 * 3)).save(p, "JPEG")
    assert pub.validate_media_for_platform("instagram", [p]) is None


def test_preflight_instagram_ratio_still_enforced(tmp_path):
    p = _write_image(tmp_path, "tall.jpg", size=(1080, 1920))  # 9:16 feed image
    result = pub.validate_media_for_platform("instagram", [p])
    assert result is not None and result.skipped is True
    assert "aspect ratio" in (result.error or "")


def test_preflight_linkedin_missing_pdf_is_skipped(tmp_path):
    """Bug 7: .pdf hit `continue` before the existence check."""
    result = pub.validate_media_for_platform("linkedin", [str(tmp_path / "gone.pdf")])
    assert result is not None and result.skipped is True
    assert "missing" in (result.error or "")


def test_preflight_linkedin_empty_pdf_is_skipped(tmp_path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    result = pub.validate_media_for_platform("linkedin", [str(p)])
    assert result is not None and result.skipped is True
    assert "empty" in (result.error or "")


def test_preflight_linkedin_oversized_pdf_is_skipped(tmp_path):
    # LinkedIn Documents API: ≤100MB
    p = _write_sparse(tmp_path, "deck.pdf", 101 * 1024 * 1024, header=b"%PDF-1.7\n")
    result = pub.validate_media_for_platform("linkedin", [p])
    assert result is not None and result.skipped is True
    assert "document limit" in (result.error or "")


def test_preflight_linkedin_non_pdf_bytes_is_skipped(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"<html>not a pdf</html>")
    result = pub.validate_media_for_platform("linkedin", [str(p)])
    assert result is not None and result.skipped is True
    assert "not a valid PDF" in (result.error or "")


# ── notebook + gpu_serial script hygiene (#302 review) ───────────────────────


def _find_repo_root() -> "_pathlib.Path | None":
    """Walk up from this file to the monorepo root (the dir holding both
    ``notebooks/`` and ``scripts/``). Returns None when the tests run outside
    the repo layout — e.g. inside the social-api image, where this file is
    /app/tests/unit/test_publishing.py and a fixed ``parents[4]`` would raise
    IndexError at import and break collection of the whole module."""
    for parent in _pathlib.Path(__file__).resolve().parents:
        if (parent / "notebooks").is_dir() and (parent / "scripts").is_dir():
            return parent
    return None


_REPO_ROOT = _find_repo_root()


def _repo_file(*parts: str) -> _pathlib.Path:
    if _REPO_ROOT is None:
        pytest.skip("repo root (notebooks/ + scripts/) not found — not running from a checkout")
    assert _REPO_ROOT is not None  # pytest.skip raises; narrows for mypy
    path = _REPO_ROOT.joinpath(*parts)
    if not path.exists():
        pytest.skip(f"{'/'.join(parts)} not present in this checkout")
    return path


def _imported_names(tree):
    names = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def test_media_validation_notebook_imports_base64():
    """Bug 4: dmr_caption uses base64 but the notebook never imported it."""
    nb_path = _repo_file("notebooks", "media_validation.ipynb")
    nb = _json.loads(nb_path.read_text())
    src = "\n".join("".join(c["source"]) if isinstance(c["source"], list) else c["source"] for c in nb["cells"] if c["cell_type"] == "code")
    tree = _ast.parse(src)
    used = {n.value.id for n in _ast.walk(tree) if isinstance(n, _ast.Attribute) and isinstance(n.value, _ast.Name)}
    imported = _imported_names(tree)
    for mod in ("base64", "httpx", "json", "io"):
        if mod in used:
            assert mod in imported, f"notebook uses {mod} without importing it"
    # Width-capped rule mirror for Meta platforms
    assert '"dim_axis": "width"' in src


def test_gpu_serial_script_imports_and_keeps_lock(tmp_path, monkeypatch):
    """Bugs 5+6: gpu_serial.py lacked `import argparse`, and the flock fd was
    closed on execvp (FD_CLOEXEC) so the lock never held across the child."""
    import importlib.util
    import subprocess as _sp
    import sys as _sys

    script = _repo_file("scripts", "gpu_serial.py")
    tree = _ast.parse(script.read_text())
    assert "argparse" in _imported_names(tree)

    lock = tmp_path / "gpu.lock"
    spec = importlib.util.spec_from_file_location("gpu_serial_t", script)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("GPU_SERIAL_LOCK", str(lock))
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "unload_dmr", lambda: [])
    # The child probes the lock with a non-blocking flock on a fresh fd: it
    # must be BUSY while the child runs (i.e. held by gpu_serial).
    probe = (
        "import fcntl,sys\n"
        f"f=open({str(lock)!r},'w')\n"
        "try:\n"
        "    fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "    sys.exit(0)\n"
        "except BlockingIOError:\n"
        "    sys.exit(7)\n"
    )
    rc = mod.hold_and_run([_sys.executable, "-c", probe])
    assert rc == 7, "GPU lock was not held while the child ran"
    # Released afterwards
    assert _sp.call([_sys.executable, "-c", probe]) == 0
    # --help works (argparse importable)
    out = _sp.run([_sys.executable, str(script), "--help"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# ── coverage tails: IG graph timeout ambiguity, IG user-token fallback ──


class _IGClient:
    def __init__(self, *, wait_exc=None, publish_exc=None):
        self._wait_exc = wait_exc
        self._publish_exc = publish_exc

    async def create_image_container(self, image_url, caption):
        return "cid-1"

    async def create_carousel_item(self, image_url):
        return "cid"

    async def create_carousel_container(self, children_ids, caption):
        return "cid-1"

    async def wait_for_container_ready(self, creation_id, timeout):
        if self._wait_exc:
            raise self._wait_exc
        return True

    async def publish_container(self, creation_id):
        if self._publish_exc:
            raise self._publish_exc
        return "media-1"


@pytest.mark.asyncio
async def test_publish_instagram_graph_timeout_not_attempted(account, post, monkeypatch):
    """Container-wait timeout → publish never attempted → not ambiguous."""
    monkeypatch.setattr(pub, "InstagramAPIClient", lambda **_: _IGClient(wait_exc=TimeoutError("container wait")))
    monkeypatch.setattr(pub, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account.meta_data = {"account_type": "business"}
    post.platform_specific = {}
    r = await pub._publish_instagram_via_graph("tok", "cap", account, post, ["/tmp/a.jpg"], ["sp/a.jpg"], None)
    assert r.success is False and r.ambiguous is False


@pytest.mark.asyncio
async def test_publish_instagram_graph_timeout_finds_live(account, post, monkeypatch):
    """Publish-request timeout but the post went live → success via find_live."""
    monkeypatch.setattr(pub, "InstagramAPIClient", lambda **_: _IGClient(publish_exc=TimeoutError("publish timeout")))
    monkeypatch.setattr(pub, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    monkeypatch.setattr(pub, "_instagram_find_live", AsyncMock(return_value={"id": "m-9", "permalink": "https://ig/p/m-9"}))
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account.meta_data = {"account_type": "business"}
    post.platform_specific = {}
    r = await pub._publish_instagram_via_graph("tok", "cap", account, post, ["/tmp/a.jpg"], ["sp/a.jpg"], None)
    assert r.success is True and r.platform_post_id == "m-9"


@pytest.mark.asyncio
async def test_publish_instagram_graph_timeout_ambiguous(account, post, monkeypatch):
    """Publish timeout with no live post found → ambiguous failure."""
    monkeypatch.setattr(pub, "InstagramAPIClient", lambda **_: _IGClient(publish_exc=TimeoutError("publish timeout")))
    monkeypatch.setattr(pub, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    monkeypatch.setattr(pub, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account.meta_data = {"account_type": "business"}
    post.platform_specific = {}
    r = await pub._publish_instagram_via_graph("tok", "cap", account, post, ["/tmp/a.jpg"], ["sp/a.jpg"], None)
    assert r.success is False and r.ambiguous is True


@pytest.mark.asyncio
async def test_publish_instagram_graph_url_uses_permalink(account, post, monkeypatch):
    """platform_url must come from Graph's permalink field — the media id
    is a Graph identifier, never something we fabricate a /p/ link from."""

    class _IGWithPermalink(_IGClient):
        async def publish_container(self, creation_id):
            return "18112569254028827"

        async def get_media_permalink(self, media_id):
            assert media_id == "18112569254028827"
            return "https://www.instagram.com/p/RealCode123/"

    monkeypatch.setattr(pub, "InstagramAPIClient", lambda **_: _IGWithPermalink())
    monkeypatch.setattr(pub, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    account.meta_data = {"account_type": "business"}
    post.platform_specific = {}
    r = await pub._publish_instagram_via_graph("tok", "cap", account, post, ["/tmp/a.jpg"], ["sp/a.jpg"], None)
    assert r.success is True
    assert r.platform_url == "https://www.instagram.com/p/RealCode123/"


@pytest.mark.asyncio
async def test_publish_instagram_graph_url_none_when_permalink_absent(account, post, monkeypatch):
    """When Graph supplies no permalink, platform_url is None — never a
    fabricated /p/<id> link pointing at an unrelated post."""

    class _IGNoPermalink(_IGClient):
        async def publish_container(self, creation_id):
            return "18112569254028827"

        async def get_media_permalink(self, media_id):
            return None

    monkeypatch.setattr(pub, "InstagramAPIClient", lambda **_: _IGNoPermalink())
    monkeypatch.setattr(pub, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    account.meta_data = {"account_type": "business"}
    post.platform_specific = {}
    r = await pub._publish_instagram_via_graph("tok", "cap", account, post, ["/tmp/a.jpg"], ["sp/a.jpg"], None)
    assert r.success is True and r.platform_url is None


class _Res2:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v[0] if isinstance(self._v, list) and self._v else (None if isinstance(self._v, list) else self._v)


class _DB2:
    def __init__(self, results=()):
        self._q = list(results)

    async def execute(self, stmt):
        return _Res2(self._q.pop(0) if self._q else None)


@pytest.mark.asyncio
async def test_resolve_ig_user_token_parent_decrypt_fails(monkeypatch):
    """FB parent token decrypt failure → warn, fall through to team lookup →
    stored token."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: (_ for _ in ()).throw(RuntimeError("bad enc")))
    parent = SimpleNamespace(platform="facebook", account_type="user", access_token_enc=b"enc")
    account = SimpleNamespace(meta_data={}, parent_account_id=uuid.uuid4(), team_id=uuid.uuid4())
    db = _DB2(results=[parent, None])
    out = await pub._resolve_ig_user_token("stored-tok", account, db)
    assert out == "stored-tok"


@pytest.mark.asyncio
async def test_resolve_ig_user_token_non_fb_parent_warns():
    """Non-Facebook parent → warning → team lookup → stored token."""
    parent = SimpleNamespace(platform="threads", account_type="user", access_token_enc=b"enc")
    account = SimpleNamespace(meta_data={}, parent_account_id=uuid.uuid4(), team_id=uuid.uuid4())
    db = _DB2(results=[parent, None])
    out = await pub._resolve_ig_user_token("stored-tok", account, db)
    assert out == "stored-tok"


@pytest.mark.asyncio
async def test_publish_tiktok_photo_options_mapping(monkeypatch):
    """Photo post maps SocialAuto tiktok options → init_photo_post kwargs."""
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_photo_post=AsyncMock(return_value={"data": {"publish_id": "ph-1"}}),
        check_publish_status=AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["photo-9"]}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _p, **kw: "https://verified.example/p.jpg")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(
        platform_specific={
            "tiktok": {
                "disable_duet": True,
                "disable_stitch": False,
                "disable_comment": True,
                "video_cover_timestamp_ms": "bad-int",
                "is_aigc": True,
                "auto_add_music": True,
                "photo_cover_index": "2",
            }
        }
    )
    r = await pub._publish_tiktok("token", "cap", account, post, ["a.jpg"], ["sp/a.jpg"])
    assert r.success is True
    kw = client.init_photo_post.await_args.kwargs
    assert kw["auto_add_music"] is True
    assert kw["photo_cover_index"] == 2
    assert kw["disable_comment"] is True and kw["is_aigc"] is True


@pytest.mark.asyncio
async def test_publish_tiktok_photo_bad_cover_index_tolerated(monkeypatch):
    """Non-int photo_cover_index → swallowed → omitted from kwargs."""
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_photo_post=AsyncMock(return_value={"data": {"publish_id": "ph-1"}}),
        check_publish_status=AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["photo-9"]}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda _p, **kw: "https://verified.example/p.jpg")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-123", username="creator", meta_data={})
    post = SimpleNamespace(
        platform_specific={
            "tiktok": {
                "video_cover_timestamp_ms": 5000,
                "photo_cover_index": "not-an-int",
            }
        }
    )
    r = await pub._publish_tiktok("token", "cap", account, post, ["a.jpg"], ["sp/a.jpg"])
    assert r.success is True
    kw = client.init_photo_post.await_args.kwargs
    assert "photo_cover_index" not in kw


# ── batch-14 tails ────────────────────────────────────────────────────


def _dispatch_account():
    return SimpleNamespace(
        platform="threads",
        account_id="a1",
        username="u1",
        access_token_enc=b"enc",
        meta_data={},
    )


def _dispatch_post():
    return SimpleNamespace(
        id=1,
        content_text="hello world",
        media_ids=[1],
        platform_specific={},
        link_url=None,
    )


def _dispatch_patches(monkeypatch):
    monkeypatch.setattr(pub, "decrypt_token", lambda _b: "tok")
    monkeypatch.setattr("app.services.content_renderer.render_post_text", lambda _p, _pl: "hi")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_resolve_media_paths", AsyncMock(return_value=["/x.jpg"]))
    monkeypatch.setattr(pub, "_resolve_media_storage_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_resolve_music_path", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "validate_media_for_platform", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_publish_to_platform_http_status_error(monkeypatch):
    _dispatch_patches(monkeypatch)
    resp = httpx.Response(500, text="server down")
    exc = httpx.HTTPStatusError("x", request=httpx.Request("POST", "https://x"), response=resp)
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=exc))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and "HTTP 500" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_to_platform_linkedin_api_error(monkeypatch):
    _dispatch_patches(monkeypatch)
    exc = pub.LinkedInAPIError(502, "bad gateway", "https://api.linkedin.com/x")
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=exc))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and "HTTP 502" in (r.error or "") and "bad gateway" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_to_platform_tiktok_api_error_clarified(monkeypatch):
    _dispatch_patches(monkeypatch)
    exc = pub.TikTokAPIError(403, "unaudited_client_can_only_post_to_private_accounts", "u")
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=exc))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and r.skipped is True


@pytest.mark.asyncio
async def test_publish_to_platform_twitter_api_error(monkeypatch):
    _dispatch_patches(monkeypatch)
    exc = pub.TwitterAPIError(500, "internal", "u", message="x exploded")
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=exc))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and "x exploded" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_to_platform_fb_group_marker_skip(monkeypatch):
    _dispatch_patches(monkeypatch)
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=Exception("permission: publish_to_groups missing")))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and r.skipped is True


@pytest.mark.asyncio
async def test_publish_to_platform_tt_ownership_marker_skip(monkeypatch):
    _dispatch_patches(monkeypatch)
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=Exception("url_ownership_unverified")))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and r.skipped is True


@pytest.mark.asyncio
async def test_publish_to_platform_generic_error(monkeypatch):
    _dispatch_patches(monkeypatch)
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(side_effect=Exception("plain boom")))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is False and r.error == "plain boom"


@pytest.mark.asyncio
async def test_publish_to_platform_spellcheck_failure_is_advisory(monkeypatch):
    _dispatch_patches(monkeypatch)
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(side_effect=RuntimeError("lt down")))
    monkeypatch.setattr(pub, "_publish_threads", AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="t1")))
    r = await pub.publish_to_platform(_dispatch_account(), _dispatch_post(), None)
    assert r.success is True


@pytest.mark.asyncio
async def test_refresh_oauth2_token_no_access_token(monkeypatch):
    account = SimpleNamespace(refresh_token_enc=b"r")
    monkeypatch.setattr(pub, "decrypt_token", lambda _b: "rt")
    import app.api.auth as auth_api

    monkeypatch.setattr(auth_api, "twitter_client", SimpleNamespace(refresh_token=AsyncMock(return_value={})))
    assert await pub._refresh_oauth2_token(account, SimpleNamespace(commit=AsyncMock())) is None


@pytest.mark.asyncio
async def test_facebook_page_token_fallbacks(monkeypatch):
    fake = _FakeAsyncClient([_FakeResponse(500, "down")])
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    assert await pub._facebook_page_token("utok", "pg") == "utok"

    fake2 = _FakeAsyncClient([_FakeResponse(200, {"data": [{"id": "other", "access_token": "x"}]})])
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake2)
    assert await pub._facebook_page_token("utok", "pg") == "utok"

    fake3 = _FakeAsyncClient([_FakeResponse(200, {"data": [{"id": "pg"}]})])
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake3)
    assert await pub._facebook_page_token("utok", "pg") == "utok"


@pytest.mark.asyncio
async def test_instagram_find_live_skips_mismatch_and_bad_timestamp():
    from datetime import UTC, datetime

    recent = datetime.now(UTC).isoformat()
    client = SimpleNamespace(
        list_recent_media=AsyncMock(
            return_value=[
                {"id": "1", "caption": "totally different post", "timestamp": recent},
                {"id": "2", "caption": "my caption", "timestamp": "not-a-date"},
                {"id": "3", "caption": "my caption", "timestamp": recent},
            ]
        )
    )
    live = await pub._instagram_find_live(client, "my caption")
    assert live and live["id"] == "3"


@pytest.mark.asyncio
async def test_publish_threads_generic_api_error(account, post, monkeypatch):
    monkeypatch.setattr(pub, "_media_public_url", lambda p, **k: "https://cdn.example.com/i.png")
    fake = _FakeAsyncClient([_FakeResponse(500, "boom")])
    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        result = await pub._publish_threads("tok", "hi", account, post, [])
    assert result.success is False
    assert "Threads publish failed" in (result.error or "")


@pytest.mark.asyncio
async def test_publish_tiktok_media_upload_photo_uses_media_upload_init(monkeypatch):
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_photo_post_media_upload=AsyncMock(return_value={"data": {"publish_id": "pm-1"}}),
        init_photo_post=AsyncMock(),
        init_video_post=AsyncMock(),
        init_video_upload=AsyncMock(),
        check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda *a, **k: "https://cdn.example.com/p.jpg")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-1", username="creator", meta_data={})
    post = SimpleNamespace(platform_specific={"tiktok": {"publish_mode": "MEDIA_UPLOAD"}})
    result = await pub._publish_tiktok("tok", "cap", account, post, ["a.jpg"], ["sp/a.jpg"])
    assert result.success is True
    client.init_photo_post_media_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_tiktok_empty_local_video(monkeypatch, tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    client = SimpleNamespace(get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}))
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    account = SimpleNamespace(account_id="o", username="c", meta_data={})
    post = SimpleNamespace(platform_specific={})
    result = await pub._publish_tiktok("tok", "cap", account, post, [str(v)], [])
    assert result.success is False and "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_tiktok_non_retryable_error_reraises(monkeypatch):
    exc = pub.TikTokAPIError(400, "quota exceeded", "u")
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_video_post=AsyncMock(side_effect=exc),
    )
    monkeypatch.setattr(pub, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(pub, "_media_public_url", lambda *a, **k: "https://cdn.example.com/v.mp4")
    account = SimpleNamespace(account_id="o", username="c", meta_data={})
    post = SimpleNamespace(platform_specific={})
    with pytest.raises(pub.TikTokAPIError):
        await pub._publish_tiktok("tok", "cap", account, post, ["v.mp4"], ["sp/v.mp4"])


@pytest.mark.asyncio
async def test_publish_tiktok_open_id_non_dict_meta(monkeypatch):
    captured = {}
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_photo_post=AsyncMock(return_value={"data": {"publish_id": "p1"}}),
        check_publish_status=AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["x1"]}}),
    )

    def _ctor(**kw):
        captured.update(kw)
        return client

    monkeypatch.setattr(pub, "TikTokAPIClient", _ctor)
    monkeypatch.setattr(pub, "_media_public_url", lambda *a, **k: "https://cdn.example.com/p.jpg")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    account = SimpleNamespace(account_id="open-9", username="c", meta_data=["not-a-dict"])
    post = SimpleNamespace(platform_specific={})
    result = await pub._publish_tiktok("tok", "cap", account, post, ["a.jpg"], ["sp/a.jpg"])
    assert result.success is True and captured["open_id"] == "open-9"


@pytest.mark.asyncio
async def test_publish_facebook_text_no_post_id(account, post, monkeypatch):
    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    fb = SimpleNamespace(create_post=AsyncMock(return_value={}))
    monkeypatch.setattr(pub, "FacebookAPIClient", lambda **kw: fb)
    result = await pub._publish_facebook("tok", "hi", account, post, [])
    assert result.success is False and "no post id" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_publish_facebook_photo_upload_group_marker(account, post, tmp_path, monkeypatch):
    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient([_FakeResponse(400, "error posting to a group: denied")])
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    result = await pub._publish_facebook("tok", "hi", account, post, [str(img)])
    assert result.success is False and result.skipped is True


@pytest.mark.asyncio
async def test_publish_facebook_feed_group_marker(account, post, tmp_path, monkeypatch):
    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "ph1"}),
            _FakeResponse(400, "publish_to_groups scope required"),
        ]
    )
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    result = await pub._publish_facebook("tok", "hi", account, post, [str(img)])
    assert result.success is False and result.skipped is True


@pytest.mark.asyncio
async def test_publish_facebook_feed_no_post_id(account, post, tmp_path, monkeypatch):
    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "ph1"}),
            _FakeResponse(200, {"id": ""}),
        ]
    )
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    result = await pub._publish_facebook("tok", "hi", account, post, [str(img)])
    assert result.success is False and "no post id" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_linkedin_sidecar_session_error(account, post, monkeypatch):
    account.platform = "linkedin"
    account.account_type = "user"
    account.meta_data = {"browser_storage_state": {"c": 1}}
    fake_client = SimpleNamespace(set_session=AsyncMock(side_effect=pub.LinkedInSidecarError(401, "no session")))
    monkeypatch.setattr(pub, "LinkedInSidecarClient", lambda: fake_client)
    result = await pub._publish_linkedin_via_sidecar(account, "text", post, [])
    assert result.success is False and "no session" in (result.error or "")


@pytest.mark.asyncio
async def test_linkedin_sidecar_post_error(account, post, monkeypatch):
    account.platform = "linkedin"
    account.account_type = "user"
    account.meta_data = {"browser_storage_state": {"c": 1}}
    post.link_url = None
    fake_client = SimpleNamespace(
        set_session=AsyncMock(),
        post_text=AsyncMock(side_effect=pub.LinkedInSidecarError(500, "send failed")),
    )
    monkeypatch.setattr(pub, "LinkedInSidecarClient", lambda: fake_client)
    result = await pub._publish_linkedin_via_sidecar(account, "text", post, [])
    assert result.success is False and "send failed" in (result.error or "")


@pytest.mark.asyncio
async def test_publish_linkedin_merges_multiple_pdfs(account, post, tmp_path, monkeypatch):
    p1 = tmp_path / "a.pdf"
    p1.write_bytes(b"%PDF-aaa")
    p2 = tmp_path / "b.pdf"
    p2.write_bytes(b"%PDF-bbb")
    client = SimpleNamespace(
        get_userinfo=AsyncMock(return_value={"sub": "li-user"}),
        create_document_post=AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="li-doc")),
    )
    monkeypatch.setattr(pub, "LinkedInAPIClient", lambda access_token: client)
    post.link_url = None
    post.content_text = "Title"
    account.meta_data = {"author_urn": "urn:li:person:x"}
    account.account_id = "urn:li:person:x"
    result = await pub._publish_linkedin("tok", "cap", account, post, [str(p1), str(p2)], [])
    assert result.success is True
    assert client.create_document_post.await_args.kwargs["pdf_bytes"] == b"%PDF-aaa%PDF-bbb"


# ── _publish_instagram dispatch ladder ────────────────────────────────


def _raise_client(**kw):
    raise RuntimeError("feed unreachable")


@pytest.mark.asyncio
async def test_ig_dispatch_dedup_guard_exception_falls_through(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "_resolve_ig_user_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_graph",
        AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="g1")),
    )
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.success is True and r.platform_post_id == "g1"


@pytest.mark.asyncio
async def test_ig_dispatch_business_login_fails_then_fb_graph(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {"login_type": "business_login"}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "_resolve_ig_user_token", AsyncMock(return_value="tok"))
    graph = AsyncMock(
        side_effect=[
            pub.PublishResult(success=False, error="gated"),
            pub.PublishResult(success=True, platform_post_id="g2"),
        ]
    )
    monkeypatch.setattr(pub, "_publish_instagram_via_graph", graph)
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.success is True and graph.await_count == 2


@pytest.mark.asyncio
async def test_ig_dispatch_business_login_ambiguous_returns(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {"login_type": "business_login"}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_graph",
        AsyncMock(return_value=pub.PublishResult(success=False, ambiguous=True, error="maybe")),
    )
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.ambiguous is True


@pytest.mark.asyncio
async def test_ig_dispatch_instagrapi_success(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "u")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "p")
    insta = AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="i1"))
    monkeypatch.setattr(pub, "_publish_instagram_via_instagrapi", insta)
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.platform_post_id == "i1" and insta.await_count == 1


@pytest.mark.asyncio
async def test_ig_dispatch_web_success(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {
        "private_api_session_id": "enc-sid",
        "private_api_csrf_token": "c",
        "private_api_ds_user_id": "d",
    }
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    web = AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="w1"))
    monkeypatch.setattr(pub, "_publish_instagram_via_web", web)
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.platform_post_id == "w1"


@pytest.mark.asyncio
async def test_ig_dispatch_sidecar_fail_returns_sidecar_result(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {"private_api_session_id": "enc-sid"}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    monkeypatch.setattr(pub, "_resolve_ig_user_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_sidecar",
        AsyncMock(return_value=pub.PublishResult(success=False, error="api down")),
    )
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_graph",
        AsyncMock(return_value=pub.PublishResult(success=False, error="graph down")),
    )
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.error == "api down"


@pytest.mark.asyncio
async def test_ig_dispatch_sidecar_fail_graph_fallback_succeeds(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {"private_api_session_id": "enc-sid"}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    monkeypatch.setattr(pub, "_resolve_ig_user_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_sidecar",
        AsyncMock(return_value=pub.PublishResult(success=False, error="api down")),
    )
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_graph",
        AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="gx")),
    )
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.platform_post_id == "gx"


@pytest.mark.asyncio
async def test_ig_dispatch_sidecar_fail_returns_web_result(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {
        "private_api_session_id": "enc-sid",
        "private_api_csrf_token": "c",
        "private_api_ds_user_id": "d",
    }
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    monkeypatch.setattr(pub, "_resolve_ig_user_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_web",
        AsyncMock(return_value=pub.PublishResult(success=False, error="web down")),
    )
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_sidecar",
        AsyncMock(return_value=pub.PublishResult(success=False, error="api down")),
    )
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_graph",
        AsyncMock(return_value=pub.PublishResult(success=False, error="graph down")),
    )
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.error == "web down"


# ── _publish_instagram_via_web configure errors ───────────────────────


def _web_client(effects):
    class _Client:
        def __init__(self):
            self._e = list(effects)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            e = self._e.pop(0)
            if isinstance(e, Exception):
                raise e
            return e

    return _Client()


def _web_account(ig_account_with_session, monkeypatch):
    ig_account_with_session.meta_data = {
        "private_api_session_id": "enc",
        "private_api_csrf_token": "c",
        "private_api_ds_user_id": "d",
    }
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    return ig_account_with_session


@pytest.mark.asyncio
async def test_ig_web_configure_http_error(ig_account_with_session, ig_post, tmp_path, monkeypatch):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(img)
    account = _web_account(ig_account_with_session, monkeypatch)
    monkeypatch.setattr(
        pub.httpx,
        "AsyncClient",
        lambda **kw: _web_client(
            [
                _FakeResponse(200, {"status": "ok", "upload_id": "u1"}),
                httpx.ConnectError("conn refused"),
            ]
        ),
    )
    r = await pub._publish_instagram_via_web(account, "cap", ig_post, [str(img)])
    assert r.success is False and "configure" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_ig_web_configure_non_200(ig_account_with_session, ig_post, tmp_path, monkeypatch):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(img)
    account = _web_account(ig_account_with_session, monkeypatch)
    monkeypatch.setattr(
        pub.httpx,
        "AsyncClient",
        lambda **kw: _web_client(
            [
                _FakeResponse(200, {"status": "ok", "upload_id": "u1"}),
                _FakeResponse(500, "server exploded"),
            ]
        ),
    )
    r = await pub._publish_instagram_via_web(account, "cap", ig_post, [str(img)])
    assert r.success is False and "Instagram web API" in (r.error or "")


@pytest.mark.asyncio
async def test_ig_web_configure_bad_json(ig_account_with_session, ig_post, tmp_path, monkeypatch):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(img)
    account = _web_account(ig_account_with_session, monkeypatch)
    monkeypatch.setattr(
        pub.httpx,
        "AsyncClient",
        lambda **kw: _web_client(
            [
                _FakeResponse(200, {"status": "ok", "upload_id": "u1"}),
                _FakeResponse(200, "not json"),
            ]
        ),
    )
    r = await pub._publish_instagram_via_web(account, "cap", ig_post, [str(img)])
    assert r.success is False and "Instagram web API" in (r.error or "")


# ── batch-15: X media block + retry ladder + FB sidecar + IG-web tails ──


def _x_account():
    return SimpleNamespace(account_id="u1", username="xu", platform="twitter", meta_data={}, account_type="user")


def _x_post(media_ids=None):
    return SimpleNamespace(
        id=1,
        content_text="hi",
        media_ids=[1] if media_ids is None else media_ids,
        platform_specific={},
        link_url=None,
    )


def _x_media_setup(monkeypatch, upload):
    plan = SimpleNamespace(has_media=True, paths=["/m.jpg"])
    monkeypatch.setattr(x_web, "plan_media", lambda _p: plan)
    monkeypatch.setattr(x_web, "prepare_media", lambda p, _w: list(p.paths))
    monkeypatch.setattr(pub, "_x_media_alt_texts", AsyncMock(return_value=[None]))
    monkeypatch.setattr(pub, "_twitter_upload_media", upload)
    client = SimpleNamespace(create_tweet=AsyncMock(return_value={"data": {"id": "t1"}}))
    monkeypatch.setattr(pub, "TwitterAPIClient", lambda **kw: client)
    return client


@pytest.mark.asyncio
async def test_publish_twitter_media_upload_success(monkeypatch):
    _x_media_setup(monkeypatch, AsyncMock(return_value="mid-1"))
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is True and r.platform_post_id == "t1"


@pytest.mark.asyncio
async def test_publish_twitter_media_alt_texts_mismatch(monkeypatch):
    _x_media_setup(monkeypatch, AsyncMock(return_value="mid-1"))
    monkeypatch.setattr(pub, "_x_media_alt_texts", AsyncMock(return_value=[]))
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is True


@pytest.mark.asyncio
async def test_publish_twitter_media_prepare_error(monkeypatch):
    _x_media_setup(monkeypatch, AsyncMock(return_value="mid-1"))
    monkeypatch.setattr(
        x_web,
        "prepare_media",
        lambda p, _w: (_ for _ in ()).throw(x_web.XWebMediaError("bad media")),
    )
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is False and r.permanent is True and "bad media" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_twitter_media_401_refresh_retry(monkeypatch):
    upload = AsyncMock(side_effect=[pub.TwitterAPIError(401, "expired", "u"), "mid-2"])
    _x_media_setup(monkeypatch, upload)
    monkeypatch.setattr(pub, "_refresh_oauth2_token", AsyncMock(return_value="nt"))
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is True and upload.await_count == 2


@pytest.mark.asyncio
async def test_publish_twitter_media_401_refresh_fails(monkeypatch):
    upload = AsyncMock(side_effect=pub.TwitterAPIError(401, "expired", "u"))
    _x_media_setup(monkeypatch, upload)
    monkeypatch.setattr(pub, "_refresh_oauth2_token", AsyncMock(return_value=None))
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is False and "X media upload failed" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_twitter_media_scope_missing_falls_back(monkeypatch):
    upload = AsyncMock(side_effect=pub.TwitterAPIError(403, pub._X_MEDIA_SCOPE_MISSING, "u"))
    _x_media_setup(monkeypatch, upload)
    fb = AsyncMock(return_value=pub.PublishResult(success=False, error="fb"))
    monkeypatch.setattr(pub, "_publish_twitter_fallbacks", fb)
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.error == "fb" and fb.await_count == 1


@pytest.mark.asyncio
async def test_publish_twitter_media_error_x_web_configured(monkeypatch):
    upload = AsyncMock(side_effect=pub.TwitterAPIError(500, "down", "u"))
    _x_media_setup(monkeypatch, upload)
    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    fb = AsyncMock(return_value=pub.PublishResult(success=False, error="fb2"))
    monkeypatch.setattr(pub, "_publish_twitter_fallbacks", fb)
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.error == "fb2" and fb.await_count == 1


@pytest.mark.asyncio
async def test_publish_twitter_media_upload_incomplete(monkeypatch):
    _x_media_setup(monkeypatch, AsyncMock(return_value="mid-1"))
    monkeypatch.setattr(x_web, "prepare_media", lambda p, _w: [])
    r = await pub._publish_twitter("tok", "hi", _x_account(), _x_post(), ["/m.jpg"], [], None)
    assert r.success is False and "incomplete" in (r.error or "").lower()


def _x_no_media_setup(monkeypatch):
    plan = SimpleNamespace(has_media=False, paths=[])
    monkeypatch.setattr(x_web, "plan_media", lambda _p: plan)
    client = SimpleNamespace(create_tweet=AsyncMock())
    monkeypatch.setattr(pub, "TwitterAPIClient", lambda **kw: client)
    return client


@pytest.mark.asyncio
async def test_publish_twitter_chunk_retry_inner_raises_original(monkeypatch):
    client = _x_no_media_setup(monkeypatch)
    err401 = pub.TwitterAPIError(401, "expired", "u")
    client.create_tweet.side_effect = [err401, pub.TwitterAPIError(500, "still bad", "u")]
    monkeypatch.setattr(pub, "_refresh_oauth2_token", AsyncMock(return_value="nt"))
    with pytest.raises(pub.TwitterAPIError) as ei:
        await pub._publish_twitter("tok", "hi", _x_account(), _x_post(media_ids=[]), [], [], None)
    assert ei.value is err401


@pytest.mark.asyncio
async def test_publish_twitter_chunk_401_refresh_none_reraises(monkeypatch):
    client = _x_no_media_setup(monkeypatch)
    err401 = pub.TwitterAPIError(401, "expired", "u")
    client.create_tweet.side_effect = err401
    monkeypatch.setattr(pub, "_refresh_oauth2_token", AsyncMock(return_value=None))
    with pytest.raises(pub.TwitterAPIError) as ei:
        await pub._publish_twitter("tok", "hi", _x_account(), _x_post(media_ids=[]), [], [], None)
    assert ei.value is err401


@pytest.mark.asyncio
async def test_publish_twitter_chunk_non_auth_error_raises(monkeypatch):
    client = _x_no_media_setup(monkeypatch)
    err500 = pub.TwitterAPIError(500, "down", "u")
    client.create_tweet.side_effect = err500
    with pytest.raises(pub.TwitterAPIError) as ei:
        await pub._publish_twitter("tok", "hi", _x_account(), _x_post(media_ids=[]), [], [], None)
    assert ei.value is err500


@pytest.mark.asyncio
async def test_publish_twitter_fallbacks_min_retry_at(monkeypatch):
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    later = datetime.now(UTC) + timedelta(hours=2)
    earlier = datetime.now(UTC) + timedelta(minutes=30)
    out = SimpleNamespace(
        status="deferred",
        retry_after=later,
        error="cap",
        first_id=None,
        tweet_ids=[],
        partial=False,
    )
    monkeypatch.setattr(x_web, "publish_via_x_web", AsyncMock(return_value=out))
    r = await pub._publish_twitter_fallbacks(
        _x_account(),
        "hi",
        ["hi"],
        _x_post(),
        SimpleNamespace(paths=[]),
        reason="x",
        retry_at=earlier,
    )
    assert r.retry_after == earlier


@pytest.mark.asyncio
async def test_publish_twitter_via_browser_generic_error(account, post, monkeypatch):
    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("lock dead")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.services.browser_orchestrator.browser_session", lambda *a, **k: _Boom())
    r = await pub._publish_twitter_via_browser(account, "hi", post, ["/m.jpg"])
    assert r.success is False and r.skipped is True
    assert "browser fallback failed" in (r.error or "")


@pytest.mark.asyncio
async def test_publish_facebook_feed_raise_for_status_raises(account, post, tmp_path, monkeypatch):
    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "ph1"}),
            _FakeResponse(500, "plain server error"),
        ]
    )
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(httpx.HTTPStatusError):
        await pub._publish_facebook("tok", "hi", account, post, [str(img)])


@pytest.mark.asyncio
async def test_publish_facebook_feed_raise_group_marker_skips(account, post, tmp_path, monkeypatch):
    class _MarkerResponse(_FakeResponse):
        def raise_for_status(self):
            raise Exception("publish_to_groups scope required")

    account.platform = "facebook"
    account.account_type = "page"
    account.account_id = "116436681562585"
    account.meta_data = {}
    post.link_url = None
    monkeypatch.setattr(pub, "_facebook_page_token", AsyncMock(return_value="ptok"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "ph1"}),
            _MarkerResponse(500, "err"),
        ]
    )
    monkeypatch.setattr(pub.httpx, "AsyncClient", lambda **kw: fake)
    result = await pub._publish_facebook("tok", "hi", account, post, [str(img)])
    assert result.success is False and result.skipped is True


@pytest.mark.asyncio
async def test_publish_facebook_via_sidecar_error(account, post, monkeypatch):
    account.meta_data = {"browser_storage_state": {"c": 1}}
    post.link_url = None
    client = SimpleNamespace(
        set_session=AsyncMock(),
        post_text=AsyncMock(side_effect=pub.FacebookSidecarError(500, "send fail")),
    )
    monkeypatch.setattr(pub, "FacebookSidecarClient", lambda: client)
    result = await pub._publish_facebook_via_sidecar(account, "hi", post, [])
    assert result.success is False and result.error == "send fail"


@pytest.mark.asyncio
async def test_ig_web_unreadable_image(ig_account_with_session, ig_post, tmp_path, monkeypatch):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not-an-image-at-all")
    account = _web_account(ig_account_with_session, monkeypatch)
    r = await pub._publish_instagram_via_web(account, "cap", ig_post, [str(bad)])
    assert r.success is False and "Failed to read image" in (r.error or "")


@pytest.mark.asyncio
async def test_ig_web_rupload_http_error(ig_account_with_session, ig_post, tmp_path, monkeypatch):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(img)
    account = _web_account(ig_account_with_session, monkeypatch)
    monkeypatch.setattr(
        pub.httpx,
        "AsyncClient",
        lambda **kw: _web_client([httpx.ConnectError("conn refused")]),
    )
    r = await pub._publish_instagram_via_web(account, "cap", ig_post, [str(img)])
    assert r.success is False and "Web API upload" in (r.error or "")


@pytest.mark.asyncio
async def test_ig_dispatch_sidecar_success(ig_account_no_session, ig_post, monkeypatch):
    ig_account_no_session.meta_data = {"private_api_session_id": "enc-sid"}
    monkeypatch.setattr(pub, "InstagramAPIClient", _raise_client)
    monkeypatch.setattr(pub._settings, "INSTAGRAM_USERNAME", "")
    monkeypatch.setattr(pub._settings, "INSTAGRAM_PASSWORD", "")
    monkeypatch.setattr(pub, "decrypt_field", lambda v: "sid" if v else None)
    sidecar = AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="s1"))
    monkeypatch.setattr(pub, "_publish_instagram_via_sidecar", sidecar)
    r = await pub._publish_instagram("tok", "cap", ig_account_no_session, ig_post, ["/x.jpg"], [], None)
    assert r.platform_post_id == "s1"


@pytest.mark.asyncio
async def test_resolve_ig_user_token_fb_user_decrypt_fails(monkeypatch):
    """Team FB user token decrypt failure → warn → stored token."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: (_ for _ in ()).throw(RuntimeError("bad enc")))
    fb_user = SimpleNamespace(platform="facebook", account_type="user", access_token_enc=b"enc")
    account = SimpleNamespace(meta_data={}, parent_account_id=None, team_id=uuid.uuid4())
    db = _DB2(results=[[fb_user]])
    out = await pub._resolve_ig_user_token("stored-tok", account, db)
    assert out == "stored-tok"


@pytest.mark.asyncio
async def test_twitter_upload_media_video_and_gif(tmp_path, monkeypatch):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"vid")
    g = tmp_path / "a.gif"
    g.write_bytes(b"GIF89a")
    client = SimpleNamespace(
        upload_media_chunked=AsyncMock(return_value="chunk-1"),
        upload_media=AsyncMock(return_value="im-1"),
        set_media_alt_text=AsyncMock(),
    )
    monkeypatch.setattr(pub, "_x_media_client", lambda tok, acct: client)
    account = SimpleNamespace(scopes=["media.write"])
    assert await pub._twitter_upload_media(str(v), access_token="t", account=account) == "chunk-1"
    assert client.upload_media_chunked.await_args.args[1] == "video/mp4"
    assert await pub._twitter_upload_media(str(g), access_token="t", account=account) == "chunk-1"
    assert client.upload_media_chunked.await_args.args[1] == "image/gif"


@pytest.mark.asyncio
async def test_twitter_upload_media_alt_text_failure_best_effort(tmp_path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    client = SimpleNamespace(
        upload_media=AsyncMock(return_value="im-1"),
        set_media_alt_text=AsyncMock(side_effect=pub.TwitterAPIError(500, "down", "u")),
    )
    monkeypatch.setattr(pub, "_x_media_client", lambda tok, acct: client)
    out = await pub._twitter_upload_media(
        str(img),
        access_token="t",
        account=SimpleNamespace(scopes=["media.write"]),
        alt_text="alt",
    )
    assert out == "im-1"


@pytest.mark.asyncio
async def test_publish_to_platform_tags_owned_links_with_utm(monkeypatch):
    """Every cloudless.gr link in published copy must carry platform-scoped
    UTMs so site leads join back to the post (post → click → lead funnel).
    The structured link_url reaches the platform tagged via a read-through
    proxy — the shared ORM object must stay untouched for other targets."""
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(pub, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_resolve_media_paths", AsyncMock(return_value=["/tmp/m.png"]))
    monkeypatch.setattr(pub, "_resolve_media_storage_paths", AsyncMock(return_value=["m.png"]))
    monkeypatch.setattr(pub, "_resolve_music_path", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "validate_media_for_platform", lambda *a, **k: None)
    monkeypatch.setattr(
        pub,
        "_build_post_text",
        lambda post, platform: "Audit? https://cloudless.gr/contact now",
    )
    captured = {}

    async def _fake_linkedin(token, text, account, post, media_paths, storage_paths):
        captured["text"] = text
        captured["link_url"] = post.link_url
        return pub.PublishResult(success=True, platform_post_id="x")

    monkeypatch.setattr(pub, "_publish_linkedin", _fake_linkedin)
    account = SimpleNamespace(platform="linkedin", access_token_enc=b"enc")
    post = SimpleNamespace(
        content_text="x",
        link_url="https://cloudless.gr/pricing",
        meta_data={"source": "visibility-era-day15"},
        media_ids=["m1"],
        id=__import__("uuid").uuid4(),
    )
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is True
    assert "utm_source=linkedin" in captured["text"]
    assert "utm_medium=organic_social" in captured["text"]
    assert "utm_campaign=visibility-era-day15" in captured["text"]
    assert f"utm_content={post.id}" in captured["text"]
    assert "utm_source=linkedin" in captured["link_url"]
    # shared ORM object untouched — other platform targets see the raw URL
    assert post.link_url == "https://cloudless.gr/pricing"


@pytest.mark.asyncio
async def test_publish_to_platform_leaves_external_links_untagged(monkeypatch):
    monkeypatch.setattr(pub, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(pub, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(pub, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(pub, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "_resolve_media_paths", AsyncMock(return_value=["/tmp/m.png"]))
    monkeypatch.setattr(pub, "_resolve_media_storage_paths", AsyncMock(return_value=["m.png"]))
    monkeypatch.setattr(pub, "_resolve_music_path", AsyncMock(return_value=None))
    monkeypatch.setattr(pub, "validate_media_for_platform", lambda *a, **k: None)
    raw = "See https://linkedin.com/company/x and https://cloudless.gr/contact"
    monkeypatch.setattr(pub, "_build_post_text", lambda post, platform: raw)
    captured = {}

    async def _fake(token, text, account, post, media_paths, storage_paths):
        captured["text"] = text
        return pub.PublishResult(success=True)

    monkeypatch.setattr(pub, "_publish_linkedin", _fake)
    account = SimpleNamespace(platform="linkedin", access_token_enc=b"enc")
    post = SimpleNamespace(content_text="", link_url=None, meta_data={}, media_ids=[])
    result = await pub.publish_to_platform(account, post, db=SimpleNamespace())
    assert result.success is True
    assert "linkedin.com/company/x?utm" not in captured["text"]
    assert "cloudless.gr/contact?utm_source=linkedin" in captured["text"]
