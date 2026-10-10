"""Tests for publish_to_platform — the dispatch hub of services/publishing.py.

Covers the messaging-channel early skip, token decrypt, empty-copy and
gibberish guards, duplicate detection, media pre-flight, music mixing,
platform dispatch arity, and the exception→PublishResult mapping.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import publishing as P


def _account(platform="twitter", **kw):
    kw.setdefault("meta_data", {})
    kw.setdefault("username", "h")
    kw.setdefault("account_id", "pid")
    kw.setdefault("account_type", "page")
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform=platform,
        access_token_enc=b"enc",
        **kw,
    )


def _post(**kw):
    kw.setdefault("media_ids", [])
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        content_text="hello world",
        hashtags=[],
        title=None,
        platform_specific={},
        scheduled_at=None,
        status="draft",
        **kw,
    )


@pytest.fixture()
def _base_patches(monkeypatch):
    """Neutralize the guard chain; per-test overrides as needed."""
    monkeypatch.setattr(P, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(P, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(P, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(P, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_resolve_media_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(P, "_resolve_media_storage_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(P, "_resolve_music_path", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "validate_media_for_platform", lambda *a, **kw: None)
    monkeypatch.setattr(P, "_build_post_text", lambda post, platform: "hello")


# ── early guards ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["whatsapp", "telegram", "viber"])
async def test_messaging_channels_skip(_base_patches, channel):
    r = await P.publish_to_platform(_account(channel), _post(), None)
    assert r.skipped and "messaging channel" in r.error


@pytest.mark.asyncio
async def test_decrypt_failure(_base_patches, monkeypatch):
    monkeypatch.setattr(P, "decrypt_token", lambda t: (_ for _ in ()).throw(RuntimeError("bad key")))
    r = await P.publish_to_platform(_account(), _post(), None)
    assert not r.success and "Token decrypt failed" in r.error


@pytest.mark.asyncio
async def test_empty_copy_guard(_base_patches, monkeypatch):
    monkeypatch.setattr(P, "_build_post_text", lambda *a: "   ")
    monkeypatch.setattr(P, "auto_correct", AsyncMock(side_effect=lambda t: t))
    r = await P.publish_to_platform(_account(), _post(), None)
    assert "empty caption" in r.error


@pytest.mark.asyncio
async def test_gibberish_guard(_base_patches, monkeypatch):
    monkeypatch.setattr(P, "detect_gibberish", AsyncMock(return_value=["gG", "clieNts"]))
    r = await P.publish_to_platform(_account(), _post(), None)
    assert r.skipped and "mangled copy" in r.error


@pytest.mark.asyncio
async def test_duplicate_guard(_base_patches, monkeypatch):
    dup = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(P, "_find_duplicate_post", AsyncMock(return_value=dup))
    r = await P.publish_to_platform(_account(), _post(), None)
    assert r.skipped and "Duplicate content" in r.error


@pytest.mark.asyncio
async def test_media_preflight_fail(_base_patches, monkeypatch):
    bad = P.PublishResult(success=False, skipped=True, error="media rejected")
    monkeypatch.setattr(P, "validate_media_for_platform", lambda *a, **kw: bad)
    r = await P.publish_to_platform(_account(), _post(), None)
    assert r is bad


@pytest.mark.asyncio
async def test_unsupported_platform(_base_patches):
    r = await P.publish_to_platform(_account("myspace"), _post(), None)
    assert "Unsupported platform" in r.error


# ── dispatch ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_dispatch_passes_db(_base_patches, monkeypatch):
    fn = AsyncMock(return_value=P.PublishResult(success=True, platform_post_id="t1"))
    monkeypatch.setattr(P, "_publish_twitter", fn)
    r = await P.publish_to_platform(_account("twitter"), _post(), "DB")
    assert r.success
    # twitter/instagram get storage_paths AND db
    assert fn.await_args.args[6] == "DB"


@pytest.mark.asyncio
async def test_linkedin_dispatch_no_db(_base_patches, monkeypatch):
    fn = AsyncMock(return_value=P.PublishResult(success=True))
    monkeypatch.setattr(P, "_publish_linkedin", fn)
    r = await P.publish_to_platform(_account("linkedin"), _post(), "DB")
    assert r.success
    assert len(fn.await_args.args) == 6  # no db arg


@pytest.mark.asyncio
async def test_music_mixing(_base_patches, monkeypatch):
    post = _post(media_ids=[uuid.uuid4()])
    monkeypatch.setattr(P, "_resolve_media_paths", AsyncMock(return_value=["/v.mp4"]))
    monkeypatch.setattr(P, "_resolve_music_path", AsyncMock(return_value="/a.mp3"))
    monkeypatch.setattr(P, "_mix_audio_into_video", AsyncMock(return_value="/mixed.mp4"))
    fn = AsyncMock(return_value=P.PublishResult(success=True))
    monkeypatch.setattr(P, "_publish_twitter", fn)
    r = await P.publish_to_platform(_account("twitter"), post, None)
    assert r.success and fn.await_args.args[4] == ["/mixed.mp4"]


# ── exception mapping ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_quota_exception(_base_patches, monkeypatch):
    class _XErr(Exception):
        status_code = None
        response_text = ""
        response_headers = {}

    monkeypatch.setattr(P, "TwitterAPIError", _XErr)
    monkeypatch.setattr(P, "_publish_twitter", AsyncMock(side_effect=_XErr("usage-capped monthly")))
    r = await P.publish_to_platform(_account("twitter"), _post(), None)
    assert not r.success and r.retry_after is not None


@pytest.mark.asyncio
async def test_twitter_generic_exception(_base_patches, monkeypatch):
    class _XErr(Exception):
        status_code = None
        response_text = ""
        response_headers = {}

    monkeypatch.setattr(P, "TwitterAPIError", _XErr)
    monkeypatch.setattr(P, "_publish_twitter", AsyncMock(side_effect=_XErr("boom")))
    r = await P.publish_to_platform(_account("twitter"), _post(), None)
    assert not r.success and "boom" in r.error


@pytest.mark.asyncio
async def test_tiktok_ownership_exception(_base_patches, monkeypatch):
    class _TErr(Exception):
        pass

    monkeypatch.setattr(P, "TikTokAPIError", _TErr)
    monkeypatch.setattr(P, "_publish_tiktok", AsyncMock(side_effect=_TErr("url_ownership_unverified")))
    r = await P.publish_to_platform(_account("tiktok"), _post(), None)
    assert r.skipped


@pytest.mark.asyncio
async def test_generic_fb_group_exception(_base_patches, monkeypatch):
    monkeypatch.setattr(P, "_publish_facebook", AsyncMock(side_effect=RuntimeError("posting to a group")))
    r = await P.publish_to_platform(_account("facebook"), _post(), None)
    assert r.skipped


@pytest.mark.asyncio
async def test_generic_unmapped_exception(_base_patches, monkeypatch):
    monkeypatch.setattr(P, "_publish_threads", AsyncMock(side_effect=RuntimeError("weird")))
    r = await P.publish_to_platform(_account("threads"), _post(), None)
    assert not r.success and "weird" in r.error


# ── per-platform publishers: threads + bluesky ────────────────────────


class _ThreadsClient:
    created = []

    def __init__(self, access_token, user_id):
        self.token = access_token
        self.uid = user_id

    async def create_text_container(self, text):
        _ThreadsClient.created.append(("text", text))
        return "cid"

    async def create_image_container(self, image_url, text):
        _ThreadsClient.created.append(("image", image_url))
        return "cid"

    async def create_video_container(self, video_url, text=None, is_carousel_item=False):
        _ThreadsClient.created.append(("video", video_url))
        return "cid"

    async def create_carousel_item(self, image_url, is_carousel_item):
        _ThreadsClient.created.append(("item", image_url))
        return "cid"

    async def create_carousel_container(self, children_ids, text):
        _ThreadsClient.created.append(("carousel", children_ids))
        return "cid"

    async def wait_for_container_ready(self, cid, timeout):
        return True

    async def publish_container(self, cid):
        return "media-1"


@pytest.mark.asyncio
async def test_publish_threads_text_only(monkeypatch):
    _ThreadsClient.created = []
    monkeypatch.setattr(P, "ThreadsAPIClient", _ThreadsClient)
    acc = _account("threads", username="cloudless.gr", account_id="th-1")
    r = await P._publish_threads("tok", "hi", acc, _post(), [], None)
    assert r.success and r.platform_post_id == "media-1"
    assert "threads.net/@cloudless.gr" in r.platform_url
    assert _ThreadsClient.created[0][0] == "text"


@pytest.mark.asyncio
async def test_publish_threads_carousel(monkeypatch, tmp_path):
    _ThreadsClient.created = []
    monkeypatch.setattr(P, "ThreadsAPIClient", _ThreadsClient)
    acc = _account("threads", username="u")
    monkeypatch.setattr(P, "_media_public_url", lambda sp, force_jpeg=False: f"https://cdn/{sp}")
    r = await P._publish_threads("tok", "hi", acc, _post(), [], ["a.jpg", "b.mp4", "c.png"])
    assert r.success
    kinds = [c[0] for c in _ThreadsClient.created]
    assert kinds == ["video", "item", "item", "carousel"] or "carousel" in kinds


@pytest.mark.asyncio
async def test_publish_threads_api_errors(monkeypatch):
    class _Err(Exception):
        status_code = 403

    monkeypatch.setattr(P, "ThreadsAPIError", _Err)

    class _Fail(_ThreadsClient):
        async def create_text_container(self, text):
            raise _Err("denied")

    monkeypatch.setattr(P, "ThreadsAPIClient", _Fail)
    r = await P._publish_threads("tok", "hi", _account("threads"), _post(), [], None)
    assert "threads_content_publish" in r.error

    class _Fail2(_ThreadsClient):
        async def create_text_container(self, text):
            raise ValueError("bad media")

    monkeypatch.setattr(P, "ThreadsAPIClient", _Fail2)
    r2 = await P._publish_threads("tok", "hi", _account("threads"), _post(), [], None)
    assert "media error" in r2.error


@pytest.mark.asyncio
async def test_publish_bluesky_happy(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 12000)

    calls = []

    class _Bsky:
        def __init__(self, handle, pw, pds_url=None):
            self.handle = handle
            calls.append(("init", pds_url))

        async def create_session(self):
            calls.append(("session",))

        async def upload_blob(self, data, mime):
            calls.append(("blob", mime))
            return {"ref": "b1"}

        async def create_post(self, text, images=None, video=None):
            calls.append(("post", text, bool(images), bool(video)))
            return {"uri": "at://did:plc:x/app.bsky.feed.post/rk1"}

    import app.services.bluesky_api as bapi

    monkeypatch.setattr(bapi, "BlueskyClient", _Bsky)
    acc = _account("bluesky", username="u.bsky.social", meta_data={"pds_url": "https://pds.custom"})
    r = await P._publish_bluesky("pw", "hello", acc, _post(), [str(img)], None)
    assert r.success and "bsky.app/profile" in r.platform_url
    assert calls[0] == ("init", "https://pds.custom")
    assert calls[-1][2] is True  # images attached


@pytest.mark.asyncio
async def test_publish_bluesky_video_and_errors(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00" * 12000)

    class _Bsky:
        def __init__(self, *a, **kw):
            pass

        async def create_session(self):
            pass

        async def upload_blob(self, data, mime):
            return {"ref": "vb"}

        async def create_post(self, text, images=None, video=None):
            self.video = video
            return {"uri": "at://x/y/rk"}

    import app.services.bluesky_api as bapi

    monkeypatch.setattr(bapi, "BlueskyClient", _Bsky)
    r = await P._publish_bluesky("pw", "t", _account("bluesky"), _post(), [str(vid)], None)
    assert r.success

    # login failure → error result
    class _BErr(Exception):
        status_code = 401
        response_text = "bad creds"

    monkeypatch.setattr(bapi, "BlueskyAPIError", _BErr)

    class _BskyFail(_Bsky):
        async def create_session(self):
            raise _BErr("nope")

    monkeypatch.setattr(bapi, "BlueskyClient", _BskyFail)
    r2 = await P._publish_bluesky("pw", "t", _account("bluesky"), _post(), [], None)
    assert not r2.success and "login failed" in r2.error.lower()
