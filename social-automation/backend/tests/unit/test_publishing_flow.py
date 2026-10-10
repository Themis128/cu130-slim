"""Tests for publish_to_platform — the dispatch hub of services/publishing.py.

Covers the messaging-channel early skip, token decrypt, empty-copy and
gibberish guards, duplicate detection, media pre-flight, music mixing,
platform dispatch arity, and the exception→PublishResult mapping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
    kw.setdefault("link_url", None)
    kw.setdefault("link_preview_override", None)
    kw.setdefault("title", None)
    kw.setdefault("platform_specific", {})
    kw.setdefault("hashtags", [])
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        content_text="hello world",
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


# ── instagram 5-path chain ────────────────────────────────────────────


def _r(success, error=None, ambiguous=False, skipped=False, pid="x1"):
    return P.PublishResult(
        success=success,
        error=error,
        ambiguous=ambiguous,
        skipped=skipped,
        platform_post_id=pid if success else None,
    )


def _ig_account(**meta):
    m = {"login_type": "fb", **meta}
    return _account("instagram", meta_data=m)


@pytest.mark.asyncio
async def test_ig_no_media_skipped():
    r = await P._publish_instagram("t", "hi", _ig_account(), _post(), [], None)
    assert r.skipped and "requires at least one" in r.error


@pytest.mark.asyncio
async def test_ig_idempotent_live(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value={"id": "999", "permalink": "https://ig/p/999"}))
    r = await P._publish_instagram("t", "hi", _ig_account(), _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "999"
    assert r.platform_url == "https://ig/p/999"


@pytest.mark.asyncio
async def test_ig_business_login_graph(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    graph = AsyncMock(return_value=_r(True, pid="g1"))
    monkeypatch.setattr(P, "_publish_instagram_via_graph", graph)
    acc = _ig_account(login_type="business_login")
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "g1"
    graph.assert_awaited_once()


@pytest.mark.asyncio
async def test_ig_business_login_ambiguous_short_circuits(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_publish_instagram_via_graph", AsyncMock(return_value=_r(False, "timeout", ambiguous=True)))
    insta = AsyncMock(return_value=_r(True))
    monkeypatch.setattr(P, "_publish_instagram_via_instagrapi", insta)
    monkeypatch.setattr(P._settings, "INSTAGRAM_USERNAME", "u")
    monkeypatch.setattr(P._settings, "INSTAGRAM_PASSWORD", "p")
    acc = _ig_account(login_type="business_login")
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert r.ambiguous and not insta.await_count  # never retry past ambiguous


@pytest.mark.asyncio
async def test_ig_chain_instagrapi_then_web(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="u", INSTAGRAM_PASSWORD="p"))
    insta = AsyncMock(return_value=_r(False, "2fa required"))
    web = AsyncMock(return_value=_r(True, pid="w1"))
    monkeypatch.setattr(P, "_publish_instagram_via_instagrapi", insta)
    monkeypatch.setattr(P, "_publish_instagram_via_web", web)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    acc = _ig_account(private_api_session_id="s", private_api_csrf_token="c", private_api_ds_user_id="d")
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "w1"
    insta.assert_awaited_once()
    web.assert_awaited_once()


@pytest.mark.asyncio
async def test_ig_sidecar_then_graph_fallback(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="", INSTAGRAM_PASSWORD=""))
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    sidecar = AsyncMock(return_value=_r(False, "upload failed"))
    graph = AsyncMock(return_value=_r(True, pid="g2"))
    monkeypatch.setattr(P, "_publish_instagram_via_sidecar", sidecar)
    monkeypatch.setattr(P, "_publish_instagram_via_graph", graph)
    monkeypatch.setattr(P, "_resolve_ig_user_token", AsyncMock(return_value="gt"))
    acc = _ig_account(private_api_session_id="s")  # sidecar session only
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "g2"


@pytest.mark.asyncio
async def test_ig_sidecar_session_expired_goes_graph(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="", INSTAGRAM_PASSWORD=""))
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P, "_publish_instagram_via_sidecar", AsyncMock(return_value=_r(False, "session expired")))
    graph = AsyncMock(return_value=_r(False, "token dead"))
    monkeypatch.setattr(P, "_publish_instagram_via_graph", graph)
    monkeypatch.setattr(P, "_resolve_ig_user_token", AsyncMock(return_value="gt"))
    acc = _ig_account(private_api_session_id="s")
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert not r.success and r.error == "token dead"


@pytest.mark.asyncio
async def test_ig_sidecar_fail_returns_web_error(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="", INSTAGRAM_PASSWORD=""))
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P, "_publish_instagram_via_web", AsyncMock(return_value=_r(False, "web 429")))
    monkeypatch.setattr(P, "_publish_instagram_via_sidecar", AsyncMock(return_value=_r(False, "sc err")))
    monkeypatch.setattr(P, "_publish_instagram_via_graph", AsyncMock(return_value=_r(False, "graph err")))
    monkeypatch.setattr(P, "_resolve_ig_user_token", AsyncMock(return_value="gt"))
    acc = _ig_account(private_api_session_id="s", private_api_csrf_token="c", private_api_ds_user_id="d")
    r = await P._publish_instagram("t", "hi", acc, _post(), [str(img)], None)
    assert r.error == "web 429"  # web error preferred over sidecar/graph


@pytest.mark.asyncio
async def test_ig_last_resort_graph(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: object())
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="", INSTAGRAM_PASSWORD=""))
    monkeypatch.setattr(P, "decrypt_field", lambda v: None)
    graph = AsyncMock(return_value=_r(True, pid="last"))
    monkeypatch.setattr(P, "_publish_instagram_via_graph", graph)
    monkeypatch.setattr(P, "_resolve_ig_user_token", AsyncMock(return_value="gt"))
    r = await P._publish_instagram("t", "hi", _ig_account(), _post(), [str(img)], None)
    assert r.platform_post_id == "last"


# ── twitter publisher ─────────────────────────────────────────────────


def _tw_plan(has_media=False, paths=None):
    return SimpleNamespace(has_media=has_media, paths=paths or [])


def _tw_exc(status, text="err", headers=None):
    e = P.TwitterAPIError(status, text, "https://x.test")
    e.headers.update({k.lower(): v for k, v in (headers or {}).items()})
    return e


@pytest.mark.asyncio
async def test_tw_media_incomplete_guard(tmp_path):
    post = _post(media_ids=[uuid.uuid4(), uuid.uuid4()])
    r = await P._publish_twitter("t", "hi", _account("twitter"), post, [], None)
    assert not r.success and "media incomplete" in r.error


@pytest.mark.asyncio
async def test_tw_plan_media_invalid(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda paths: (_ for _ in ()).throw(xw.XWebMediaError("bad fmt")))
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), ["x.png"], None)
    assert not r.success and r.permanent and "media invalid" in r.error


@pytest.mark.asyncio
async def test_tw_happy_single_tweet(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan())
    client = SimpleNamespace(create_tweet=AsyncMock(return_value={"data": {"id": "t100"}}))
    monkeypatch.setattr(P, "TwitterAPIClient", lambda access_token: client)
    acc = _account("twitter", username="cu_dev")
    r = await P._publish_twitter("t", "hello", acc, _post(), [], None)
    assert r.success and r.platform_post_id == "t100"
    assert r.platform_url == "https://twitter.com/cu_dev/status/t100"
    client.create_tweet.assert_awaited_once()


@pytest.mark.asyncio
async def test_tw_thread_partial_success_on_quota(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan())
    monkeypatch.setattr(P, "_split_thread", lambda t: ["c1", "c2", "c3"])

    calls = []

    async def _tweet(text, reply_tweet_id=None, media_ids=None):
        calls.append(reply_tweet_id)
        if len(calls) == 2:
            raise _tw_exc(402, "credits depleted")
        return {"data": {"id": f"t{len(calls)}"}}

    client = SimpleNamespace(create_tweet=_tweet)
    monkeypatch.setattr(P, "TwitterAPIClient", lambda access_token: client)
    r = await P._publish_twitter("t", "x", _account("twitter"), _post(), [], None)
    assert r.success and r.platform_post_id == "t1"
    assert r.platform_meta["x_thread"]["incomplete"] is True
    assert r.platform_meta["x_thread"]["posted"] == 1


@pytest.mark.asyncio
async def test_tw_quota_first_tweet_goes_fallbacks(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan())

    async def _tweet(**kw):
        raise _tw_exc(429, "usage capped", {"x-app-limit-24hour-reset": "1700000000"})

    monkeypatch.setattr(P, "TwitterAPIClient", lambda access_token: SimpleNamespace(create_tweet=_tweet))
    fb = AsyncMock(return_value=_r(True, pid="fb1"))
    monkeypatch.setattr(P, "_publish_twitter_fallbacks", fb)
    monkeypatch.setattr(P, "_x_retry_at", lambda e: "2025-01-01T00:00:00Z")
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), [], None)
    assert r.success
    assert fb.await_args.kwargs["retry_at"] == "2025-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_tw_401_refresh_retry(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan())
    attempts = []

    async def _tweet(text, reply_tweet_id=None, media_ids=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise _tw_exc(401, "expired")
        return {"data": {"id": "t2"}}

    client = SimpleNamespace(create_tweet=_tweet)
    monkeypatch.setattr(P, "TwitterAPIClient", lambda access_token: client)
    monkeypatch.setattr(P, "_refresh_oauth2_token", AsyncMock(return_value="new-tok"))
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), [], None)
    assert r.success and r.platform_post_id == "t2" and len(attempts) == 2


@pytest.mark.asyncio
async def test_tw_media_upload_chain(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan(True, [str(img)]))
    monkeypatch.setattr(xw, "prepare_media", lambda plan, wd: plan.paths)
    monkeypatch.setattr(P, "_x_media_alt_texts", AsyncMock(return_value=["alt"]))
    upload = AsyncMock(return_value="m-1")
    monkeypatch.setattr(P, "_twitter_upload_media", upload)
    seen = {}

    async def _tweet(text, reply_tweet_id=None, media_ids=None):
        seen["media"] = media_ids
        return {"data": {"id": "t9"}}

    monkeypatch.setattr(P, "TwitterAPIClient", lambda access_token: SimpleNamespace(create_tweet=_tweet))
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), [str(img)], None)
    assert r.success and seen["media"] == ["m-1"]


@pytest.mark.asyncio
async def test_tw_media_upload_quota_fallback(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan(True, [str(img)]))
    monkeypatch.setattr(xw, "prepare_media", lambda plan, wd: plan.paths)
    monkeypatch.setattr(P, "_x_media_alt_texts", AsyncMock(return_value=[None]))
    monkeypatch.setattr(P, "_twitter_upload_media", AsyncMock(side_effect=_tw_exc(402, "credits")))
    fb = AsyncMock(return_value=_r(True, pid="fb2"))
    monkeypatch.setattr(P, "_publish_twitter_fallbacks", fb)
    monkeypatch.setattr(P, "_x_retry_at", lambda e: None)
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), [str(img)], None)
    assert r.success and fb.await_count == 1


@pytest.mark.asyncio
async def test_tw_media_upload_hard_fail(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "plan_media", lambda p: _tw_plan(True, [str(img)]))
    monkeypatch.setattr(xw, "prepare_media", lambda plan, wd: plan.paths)
    monkeypatch.setattr(xw, "is_configured", lambda: False)
    monkeypatch.setattr(P, "_x_media_alt_texts", AsyncMock(return_value=[None]))
    monkeypatch.setattr(P, "_twitter_upload_media", AsyncMock(side_effect=_tw_exc(500, "server boom")))
    r = await P._publish_twitter("t", "hi", _account("twitter"), _post(), [str(img)], None)
    assert not r.success and "not posting without media" in r.error


# ── facebook publisher ────────────────────────────────────────────────


class _FBHttp:
    """Fake httpx.AsyncClient for the photo-upload flow."""

    posts = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, files=None):
        _FBHttp.posts.append((url, data, bool(files)))
        status, payload = _FBHttp.responses.get(url, (200, {"id": "ph1"}))
        return SimpleNamespace(
            status_code=status,
            text=str(payload),
            json=lambda: payload,
            raise_for_status=lambda: None,
        )


_FBHttp.responses = {}


@pytest.mark.asyncio
async def test_fb_group_skip():
    acc = _account("facebook", account_type="group")
    r = await P._publish_facebook("t", "hi", acc, _post(), [], None)
    assert r.skipped


@pytest.mark.asyncio
async def test_fb_personal_sidecar(monkeypatch):
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: True)
    sidecar = AsyncMock(return_value=_r(True, pid="sc1"))
    monkeypatch.setattr(P, "_publish_facebook_via_sidecar", sidecar)
    acc = _account("facebook", account_type="user")
    r = await P._publish_facebook("t", "hi", acc, _post(), [], None)
    assert r.platform_post_id == "sc1"
    sidecar.assert_awaited_once()


@pytest.mark.asyncio
async def test_fb_page_text_post(monkeypatch):
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    fb = SimpleNamespace(create_post=AsyncMock(return_value={"id": "1_2"}))
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: fb)
    acc = _account("facebook", account_id="pg1")
    r = await P._publish_facebook("t", "hello", acc, _post(), [], None)
    assert r.success and r.platform_post_id == "1_2"
    assert "facebook.com/1_2" in r.platform_url


@pytest.mark.asyncio
async def test_fb_photo_album_flow(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    fb = SimpleNamespace(create_post=AsyncMock(return_value={"id": "txt"}))
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: fb)
    monkeypatch.setattr(P.httpx, "AsyncClient", _FBHttp)
    _FBHttp.posts = []
    _FBHttp.responses = {
        f"{P.FACEBOOK_GRAPH_BASE}/{P.FACEBOOK_GRAPH_VERSION}/pg1/photos": (200, {"id": "ph1"}),
        f"{P.FACEBOOK_GRAPH_BASE}/{P.FACEBOOK_GRAPH_VERSION}/pg1/feed": (200, {"id": "alb1"}),
    }
    acc = _account("facebook", account_id="pg1")
    r = await P._publish_facebook("t", "hi", acc, _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "alb1"
    urls = [p[0] for p in _FBHttp.posts]
    assert any(u.endswith("/photos") for u in urls)
    assert any(u.endswith("/feed") for u in urls)


@pytest.mark.asyncio
async def test_fb_photo_all_fail_text_fallback(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    fb = SimpleNamespace(create_post=AsyncMock(return_value={"id": "txt9"}))
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: fb)
    monkeypatch.setattr(P.httpx, "AsyncClient", _FBHttp)
    _FBHttp.posts = []
    _FBHttp.responses = {
        f"{P.FACEBOOK_GRAPH_BASE}/{P.FACEBOOK_GRAPH_VERSION}/pg1/photos": (400, {"error": "bad"}),
    }
    acc = _account("facebook", account_id="pg1")
    r = await P._publish_facebook("t", "hi", acc, _post(), [str(img)], None)
    assert r.success and r.platform_post_id == "txt9"


@pytest.mark.asyncio
async def test_fb_page_token_group_marker(monkeypatch):
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: False)
    marker = P._FB_GROUP_MARKERS[0]
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(side_effect=Exception(f"graph says {marker}")))
    acc = _account("facebook", account_id="pg1")
    r = await P._publish_facebook("t", "hi", acc, _post(), [], None)
    assert r.skipped


@pytest.mark.asyncio
async def test_fb_page_token_other_error_raises(monkeypatch):
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda a: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(side_effect=Exception("network down")))
    acc = _account("facebook", account_id="pg1")
    with pytest.raises(Exception, match="network down"):
        await P._publish_facebook("t", "hi", acc, _post(), [], None)


# ── linkedin publisher ────────────────────────────────────────────────


def _li_client(**over):
    base = dict(
        _author_urn=lambda aid, atype: f"urn:li:{atype}:{aid}",
        create_post=AsyncMock(return_value=P.PublishResult(success=True, platform_post_id="li-txt")),
        create_video_post=AsyncMock(return_value=P.PublishResult(success=True, platform_post_id="li-vid")),
        create_document_post=AsyncMock(return_value=P.PublishResult(success=True, platform_post_id="li-doc")),
        create_multi_image_post=AsyncMock(return_value=P.PublishResult(success=True, platform_post_id="li-img")),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_li_sidecar_success(monkeypatch):
    monkeypatch.setattr(P, "_has_linkedin_browser_session", lambda a: True)
    sidecar = AsyncMock(return_value=_r(True, pid="sc-li"))
    monkeypatch.setattr(P, "_publish_linkedin_via_sidecar", sidecar)
    r = await P._publish_linkedin("t", "hi", _account("linkedin"), _post(), [], None)
    assert r.platform_post_id == "sc-li"


@pytest.mark.asyncio
async def test_li_sidecar_fail_falls_to_api(monkeypatch):
    monkeypatch.setattr(P, "_has_linkedin_browser_session", lambda a: True)
    monkeypatch.setattr(P, "_publish_linkedin_via_sidecar", AsyncMock(return_value=_r(False, "sidecar down")))
    client = _li_client()
    monkeypatch.setattr(P, "LinkedInAPIClient", lambda access_token: client)
    r = await P._publish_linkedin("t", "hi", _account("linkedin"), _post(), [], None)
    assert r.success and r.platform_post_id == "li-txt"
    client.create_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_li_video_post(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"

    vid.write_bytes(b"\x00" * 12000)
    monkeypatch.setattr(P, "_has_linkedin_browser_session", lambda a: False)
    client = _li_client()
    monkeypatch.setattr(P, "LinkedInAPIClient", lambda access_token: client)
    r = await P._publish_linkedin("t", "hi", _account("linkedin"), _post(), [str(vid)], None)
    assert r.platform_post_id == "li-vid"
    assert client.create_video_post.await_args.kwargs["video_bytes"] == b"\x00" * 12000


@pytest.mark.asyncio
async def test_li_pdf_and_multi_image(monkeypatch, tmp_path):
    pdf = tmp_path / "c.pdf"

    pdf.write_bytes(b"%PDF-1.4" + b"\x00" * 12000)
    i1 = tmp_path / "a.png"

    i1.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    i2 = tmp_path / "b.png"

    i2.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "_has_linkedin_browser_session", lambda a: False)
    client = _li_client()
    monkeypatch.setattr(P, "LinkedInAPIClient", lambda access_token: client)
    monkeypatch.setattr(P, "_images_to_pdf", lambda paths, title: b"pdf-bytes")
    acc = _account("linkedin")

    r = await P._publish_linkedin("t", "hi", acc, _post(), [str(pdf)], None)
    assert r.platform_post_id == "li-doc"

    r2 = await P._publish_linkedin("t", "hi", acc, _post(), [str(i1), str(i2)], None)
    assert r2.platform_post_id == "li-doc"
    assert client.create_document_post.await_args.kwargs["pdf_bytes"] == b"pdf-bytes"

    r3 = await P._publish_linkedin("t", "hi", acc, _post(), [str(i1)], None)
    assert r3.platform_post_id == "li-img"


@pytest.mark.asyncio
async def test_li_link_post_with_override(monkeypatch):
    monkeypatch.setattr(P, "_has_linkedin_browser_session", lambda a: False)
    client = _li_client()
    monkeypatch.setattr(P, "LinkedInAPIClient", lambda access_token: client)
    post = _post(link_url="https://cloudless.gr", link_preview_override={"title": "T", "description": "D"})
    r = await P._publish_linkedin("t", "hi", _account("linkedin"), post, [], None)
    assert r.success
    kw = client.create_post.await_args.kwargs
    assert kw["link_url"] == "https://cloudless.gr" and kw["link_title"] == "T"


@pytest.mark.asyncio
async def test_li_sidecar_no_session():
    r = await P._publish_linkedin_via_sidecar(_account("linkedin", meta_data={}), "hi", _post(), [])
    assert not r.success and "browser session" in r.error


@pytest.mark.asyncio
async def test_li_sidecar_org_and_personal(monkeypatch, tmp_path):
    img = tmp_path / "p.png"

    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    calls = []

    class _Sidecar:
        async def set_session(self, storage):
            calls.append(("session",))

        async def company_post_image(self, vanity, images, message):
            calls.append(("org-img", vanity, len(images)))
            return {"url": "https://li/co/1", "post_id": "c1"}

        async def company_post_text(self, vanity, message):
            calls.append(("org-txt", vanity))
            return {"url": "https://li/co/2", "post_id": "c2"}

        async def post_image(self, images, message):
            calls.append(("p-img", len(images)))
            return {"url": "https://li/p/1"}

        async def post_link(self, url, message):
            calls.append(("p-link", url))
            return {"url": "https://li/p/2"}

        async def post_text(self, message):
            calls.append(("p-txt",))
            return {"url": "https://li/p/3"}

    monkeypatch.setattr(P, "LinkedInSidecarClient", _Sidecar)

    org = _account("linkedin", account_type="organization", meta_data={"browser_storage_state": {"s": 1}, "vanity_name": "cloudless_gr"})
    r = await P._publish_linkedin_via_sidecar(org, "hi", _post(), [str(img)])
    assert r.success and r.platform_post_id == "c1"
    assert calls[1] == ("org-img", "cloudless-gr", 1)  # _ → - normalization

    r2 = await P._publish_linkedin_via_sidecar(org, "hi", _post(), [])
    assert r2.platform_post_id == "c2"

    person = _account("linkedin", account_type="person", meta_data={"browser_storage_state": {"s": 1}})
    r3 = await P._publish_linkedin_via_sidecar(person, "hi", _post(), [str(img)])
    assert r3.success and r3.platform_url == "https://li/p/1"
    await P._publish_linkedin_via_sidecar(person, "hi", _post(link_url="https://x.io"), [])
    assert calls[-1] == ("p-link", "https://x.io")


@pytest.mark.asyncio
async def test_li_sidecar_org_no_vanity(monkeypatch):
    class _Sidecar:
        async def set_session(self, storage):
            return {}

    monkeypatch.setattr(P, "LinkedInSidecarClient", _Sidecar)
    org = _account("linkedin", account_type="organization", username=None, meta_data={"browser_storage_state": {"s": 1}})
    r = await P._publish_linkedin_via_sidecar(org, "hi", _post(), [])
    assert not r.success and "vanity" in r.error


def test_li_author_urn_helper():
    client = SimpleNamespace(_author_urn=lambda aid, t: f"urn:{t}:{aid}")
    acc = _account("linkedin", meta_data={"author_urn": "urn:custom"})
    assert P._linkedin_author_urn(acc, client) == "urn:custom"
    acc2 = _account("linkedin", account_id="42", meta_data={"account_type": "organization"})
    assert P._linkedin_author_urn(acc2, client) == "urn:organization:42"
    assert P._has_linkedin_browser_session(_account("linkedin", meta_data={"browser_storage_state": {}})) is False
    assert P._has_linkedin_browser_session(_account("linkedin", meta_data={"browser_storage_state": {"x": 1}})) is True


# ── tiktok publisher + status poller ──────────────────────────────────


def _tt_client(**over):
    base = dict(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}}),
        init_video_post=AsyncMock(return_value={"data": {"publish_id": "pub-1", "upload_url": "https://up/1"}}),
        init_video_upload=AsyncMock(return_value={"data": {"publish_id": "pub-1", "upload_url": "https://up/1"}}),
        init_photo_post=AsyncMock(return_value={"data": {"publish_id": "pub-p"}}),
        init_photo_post_media_upload=AsyncMock(return_value={"data": {"publish_id": "pub-p"}}),
        upload_video_file=AsyncMock(return_value=None),
        check_publish_status=AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["v123"]}}),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _tt_post(**opts):
    return _post(platform_specific={"tiktok": opts})


@pytest.mark.asyncio
async def test_tt_multi_video_skip(tmp_path):
    v1 = tmp_path / "a.mp4"

    v1.write_bytes(b"\x00" * 12000)
    v2 = tmp_path / "b.mp4"

    v2.write_bytes(b"\x00" * 12000)
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), [str(v1), str(v2)], None)
    assert r.skipped and "single video" in r.error


@pytest.mark.asyncio
async def test_tt_bad_publish_mode():
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(publish_mode="BOGUS"), [], None)
    assert "publish_mode" in r.error


@pytest.mark.asyncio
async def test_tt_resume_existing_publish_id(monkeypatch):
    poll = AsyncMock(return_value=_r(True, pid="resumed"))
    monkeypatch.setattr(P, "_poll_tiktok_publish_status", poll)
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: _tt_client())
    # v_inbox prefix → resume as MEDIA_UPLOAD
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(publish_id="v_inbox_abc"), [], None)
    assert r.platform_post_id == "resumed"
    assert poll.await_args.args[2] == "MEDIA_UPLOAD"
    # non-inbox id → resume as DIRECT_POST
    await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(publish_id="v_pub_xyz"), [], None)
    assert poll.await_args.args[2] == "DIRECT_POST"


@pytest.mark.asyncio
async def test_tt_privacy_validation(monkeypatch):
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: _tt_client())
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(privacy_level="FRIENDS"), ["p.png"], ["p.png"])
    assert "privacy_level must be one of" in r.error


@pytest.mark.asyncio
async def test_tt_photo_no_public_url(monkeypatch):
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: _tt_client())
    monkeypatch.setattr(P, "_media_public_url", lambda sp: None)
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), ["p.png"], ["p.png"])
    assert "public media URLs" in r.error


@pytest.mark.asyncio
async def test_tt_video_file_upload_happy(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"

    vid.write_bytes(b"\x00" * 12000)
    client = _tt_client()
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: client)
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    import app.services.tiktok_api as tapi

    monkeypatch.setattr(tapi, "validate_tiktok_video_constraints", lambda p: None)
    monkeypatch.setattr(tapi, "_video_chunk_plan", lambda size: (1000000, 2))
    monkeypatch.setattr(P, "_poll_tiktok_publish_status", AsyncMock(return_value=_r(True, pid="v123")))
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), [str(vid)], ["v.mp4"])
    assert r.success
    client.init_video_post.assert_awaited_once()
    kw = client.init_video_post.await_args.kwargs
    assert kw["source"] == "FILE_UPLOAD" and kw["video_size"] == 12000
    client.upload_video_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_tt_unaudited_falls_to_media_upload(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"

    vid.write_bytes(b"\x00" * 12000)
    marker = P._TT_UNAUDITED_MARKERS[0]

    class _Err(Exception):
        pass

    calls = []

    async def _init_video_post(**kw):
        calls.append(kw)
        raise P.TikTokAPIError(400, f"{marker} bad", "u")

    client = _tt_client(init_video_post=_init_video_post)
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: client)
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    import app.services.tiktok_api as tapi

    monkeypatch.setattr(tapi, "validate_tiktok_video_constraints", lambda p: None)
    monkeypatch.setattr(tapi, "_video_chunk_plan", lambda size: (1000000, 2))
    monkeypatch.setattr(P, "_poll_tiktok_publish_status", AsyncMock(return_value=_r(True, pid="inbox")))
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), [str(vid)], ["v.mp4"])
    assert r.success
    client.init_video_upload.assert_awaited_once()  # MEDIA_UPLOAD fallback


@pytest.mark.asyncio
async def test_tt_init_error_clarified(monkeypatch):
    marker = P._TT_OWNERSHIP_MARKERS[0]
    client = _tt_client(init_photo_post=AsyncMock(return_value={"error": {"code": "x", "message": marker}}))
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: client)
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), ["p.png"], ["p.png"])
    assert not r.success and r.skipped and "init failed" in r.error


@pytest.mark.asyncio
async def test_tt_photo_post_happy(monkeypatch):
    client = _tt_client()
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: client)
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    monkeypatch.setattr(P, "_poll_tiktok_publish_status", AsyncMock(return_value=_r(True, pid="ph")))
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), ["p.png"], ["p.png"])
    assert r.success
    kw = client.init_photo_post.await_args.kwargs
    assert kw["photo_urls"] == ["https://cdn/p.png"]


@pytest.mark.asyncio
async def test_tt_pull_from_url_video(monkeypatch):
    client = _tt_client()
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **kw: client)
    monkeypatch.setattr(P, "_media_public_url", lambda sp: f"https://cdn/{sp}")
    monkeypatch.setattr(P, "_poll_tiktok_publish_status", AsyncMock(return_value=_r(True, pid="v")))
    # media_paths has .mp4 but local file doesn't exist → PULL_FROM_URL
    r = await P._publish_tiktok("t", "hi", _account("tiktok"), _tt_post(), ["/missing/v.mp4"], ["v.mp4"])
    assert r.success
    kw = client.init_video_post.await_args.kwargs
    assert kw["source"] == "PULL_FROM_URL" and kw["video_url"] == "https://cdn/v.mp4"


@pytest.mark.asyncio
async def test_tt_poll_terminal_states(monkeypatch):
    monkeypatch.setattr(P.asyncio, "sleep", AsyncMock()) if hasattr(P, "asyncio") else None
    import asyncio as _a

    monkeypatch.setattr(_a, "sleep", AsyncMock())

    # MEDIA_UPLOAD + SEND_TO_USER_INBOX → success with profile link
    client = _tt_client(check_publish_status=AsyncMock(return_value={"data": {"status": "SEND_TO_USER_INBOX"}}))
    r = await P._poll_tiktok_publish_status(client, "v_inbox_x", "MEDIA_UPLOAD", "cloudless.gr")
    assert r.success and "tiktok.com/@cloudless.gr" in r.platform_url
    assert r.platform_meta["tiktok"]["status"] == "SEND_TO_USER_INBOX"

    # PUBLISH_COMPLETE with public id → video url
    client2 = _tt_client(check_publish_status=AsyncMock(return_value={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["v999"]}}))
    r2 = await P._poll_tiktok_publish_status(client2, "p1", "DIRECT_POST", "cloudless.gr")
    assert r2.success and r2.platform_post_id == "v999"
    assert "/video/v999" in r2.platform_url

    # FAILED with ownership marker → skipped
    marker = P._TT_OWNERSHIP_MARKERS[0]
    client3 = _tt_client(check_publish_status=AsyncMock(return_value={"data": {"status": "FAILED", "fail_reason": marker}}))
    r3 = await P._poll_tiktok_publish_status(client3, "p2", "DIRECT_POST", "u")
    assert not r3.success and r3.skipped
    assert r3.platform_meta["tiktok"]["fail_reason"] == marker

    # Timeout → non-skipped failure with detail
    client4 = _tt_client(check_publish_status=AsyncMock(return_value={"data": {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 5}}))
    r4 = await P._poll_tiktok_publish_status(client4, "p3", "DIRECT_POST", "u", attempts=2, interval_sec=0)
    assert not r4.success and not r4.skipped
    assert "uploaded_bytes=5" in r4.error and "timeout" in r4.error


# ── twitter fallbacks + browser path ──────────────────────────────────


def _xw_out(status, first_id=None, error=None, retry_after=None, tweet_ids=None, partial=False):
    return SimpleNamespace(status=status, first_id=first_id, error=error, retry_after=retry_after, tweet_ids=tweet_ids or [], partial=partial)


@pytest.mark.asyncio
async def test_twf_xweb_ok(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    monkeypatch.setattr(xw, "publish_via_x_web", AsyncMock(return_value=_xw_out("ok", first_id="w1", tweet_ids=["w1", "w2"])))
    acc = _account("twitter", username="h")
    r = await P._publish_twitter_fallbacks(acc, "t", ["c1"], _post(), _tw_plan(), reason="quota")
    assert r.success and r.platform_post_id == "w1"
    assert r.platform_meta["x_web"]["provider"] == "x_web_tweety"


@pytest.mark.asyncio
async def test_twf_xweb_partial_and_deferred(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    acc = _account("twitter", username="h")
    monkeypatch.setattr(xw, "publish_via_x_web", AsyncMock(return_value=_xw_out("ok", first_id="w1", partial=True, error="cap hit")))
    r = await P._publish_twitter_fallbacks(acc, "t", ["c1"], _post(), _tw_plan(), reason="q")
    assert r.success and r.platform_meta["x_web"]["thread_incomplete"]

    monkeypatch.setattr(xw, "publish_via_x_web", AsyncMock(return_value=_xw_out("deferred", error="daily cap", retry_after="2030-01-01")))
    r2 = await P._publish_twitter_fallbacks(acc, "t", ["c1"], _post(), _tw_plan(), reason="q")
    assert not r2.success and r2.retry_after == "2030-01-01"


@pytest.mark.asyncio
async def test_twf_xweb_permanent(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    for st in ("tripped", "media_error", "identity", "too_long", "ambiguous"):
        monkeypatch.setattr(xw, "publish_via_x_web", AsyncMock(return_value=_xw_out(st, error=f"perm {st}")))
        r = await P._publish_twitter_fallbacks(_account("twitter"), "t", ["c1"], _post(), _tw_plan(), reason="q")
        assert not r.success and r.permanent and r.error == f"perm {st}"


@pytest.mark.asyncio
async def test_twf_video_no_xweb_defers(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: False)
    r = await P._publish_twitter_fallbacks(
        _account("twitter"),
        "t",
        ["c1"],
        _post(),
        _tw_plan(True, ["v.mp4"]) if False else SimpleNamespace(has_media=True, paths=["v.mp4"], kind="video"),
        reason="quota",
    )
    assert not r.success and r.retry_after is not None
    assert "browser bridge cannot attach video" in r.error


@pytest.mark.asyncio
async def test_twf_browser_paths(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    monkeypatch.setattr(xw, "publish_via_x_web", AsyncMock(return_value=_xw_out("transient", error="xweb flaky")))

    # browser success → returned
    monkeypatch.setattr(P, "_publish_twitter_via_browser", AsyncMock(return_value=_r(True, pid="b1")))
    r = await P._publish_twitter_fallbacks(_account("twitter"), "t", ["c1"], _post(), SimpleNamespace(kind="image", paths=["p.png"]), reason="q")
    assert r.platform_post_id == "b1"

    # browser hard fail → error merged with web_error
    monkeypatch.setattr(P, "_publish_twitter_via_browser", AsyncMock(return_value=_r(False, "wrong acct")))
    r2 = await P._publish_twitter_fallbacks(_account("twitter"), "t", ["c1"], _post(), SimpleNamespace(kind="image", paths=["p.png"]), reason="q")
    assert not r2.success and "xweb flaky" in r2.error and "wrong acct" in r2.error

    # browser skipped → capacity defer
    monkeypatch.setattr(P, "_publish_twitter_via_browser", AsyncMock(return_value=_r(False, "busy", skipped=True)))
    r3 = await P._publish_twitter_fallbacks(_account("twitter"), "t", ["c1"], _post(), SimpleNamespace(kind="image", paths=["p.png"]), reason="q")
    assert not r3.success and r3.retry_after is not None


class _BrowserSession:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _bridge_client(**over):
    base = dict(
        session_status=AsyncMock(return_value={"platform": "twitter"}),
        start_session=AsyncMock(return_value={}),
        is_twitter_logged_in=AsyncMock(return_value={"logged_in": True, "handle": "cu_dev"}),
        twitter_login=AsyncMock(return_value={"status": "logged_in"}),
        post_tweet=AsyncMock(return_value={"status": "ok", "url": "https://x.com/cu_dev/status/555"}),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _patch_browser(monkeypatch, client):
    import app.services.browser_bridge as bb
    import app.services.browser_orchestrator as bo

    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *a, **kw: client)
    monkeypatch.setattr(bo, "browser_session", lambda *a, **kw: _BrowserSession())


@pytest.mark.asyncio
async def test_twb_rejects_non_image_media(tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00" * 12000)
    r = await P._publish_twitter_via_browser(_account("twitter"), "t", _post(), [str(vid)])
    assert not r.success and "cannot be attached" in r.error


@pytest.mark.asyncio
async def test_twb_happy(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    client = _bridge_client()
    _patch_browser(monkeypatch, client)
    acc = _account("twitter", username="cu_dev")
    r = await P._publish_twitter_via_browser(acc, "hi", _post(), [str(img)])
    assert r.success and r.platform_post_id == "555"
    client.post_tweet.assert_awaited_once()


@pytest.mark.asyncio
async def test_twb_platform_takeover_and_login(monkeypatch):
    client = _bridge_client(
        session_status=AsyncMock(return_value={"platform": "messenger"}),
        is_twitter_logged_in=AsyncMock(
            side_effect=[
                {"logged_in": False},
                {"logged_in": True, "handle": "cu_dev"},
            ]
        ),
    )
    _patch_browser(monkeypatch, client)
    monkeypatch.setattr(
        P,
        "get_settings",
        lambda: SimpleNamespace(BROWSER_BRIDGE_URL="http://b", TWITTER_LOGIN_USERNAME="", TWITTER_LOGIN_EMAIL="e@x", TWITTER_LOGIN_PASSWORD="pw"),
    )
    acc = _account("twitter", username="cu_dev")
    r = await P._publish_twitter_via_browser(acc, "hi", _post(), [])
    assert r.success
    client.start_session.assert_awaited_once()
    client.twitter_login.assert_awaited_once()


@pytest.mark.asyncio
async def test_twb_identity_mismatch_fails(monkeypatch):
    client = _bridge_client(is_twitter_logged_in=AsyncMock(return_value={"logged_in": True, "handle": "other_guy"}))
    _patch_browser(monkeypatch, client)
    monkeypatch.setattr(
        P, "get_settings", lambda: SimpleNamespace(BROWSER_BRIDGE_URL="http://b", TWITTER_LOGIN_USERNAME="", TWITTER_LOGIN_EMAIL="", TWITTER_LOGIN_PASSWORD="")
    )
    acc = _account("twitter", username="cu_dev")
    r = await P._publish_twitter_via_browser(acc, "hi", _post(), [])
    assert not r.success and "@other_guy" in r.error

    # undetectable handle while logged in → fail closed
    client2 = _bridge_client(is_twitter_logged_in=AsyncMock(return_value={"logged_in": True, "handle": None}))
    _patch_browser(monkeypatch, client2)
    r2 = await P._publish_twitter_via_browser(acc, "hi", _post(), [])
    assert not r2.success and "undetectable" in r2.error


@pytest.mark.asyncio
async def test_twb_login_fail_and_post_fail(monkeypatch):
    import app.services.browser_bridge as bb

    client = _bridge_client(
        session_status=AsyncMock(return_value={"platform": "messenger"}), start_session=AsyncMock(side_effect=bb.BrowserBridgeError("busy", "detail-x"))
    )
    _patch_browser(monkeypatch, client)
    monkeypatch.setattr(
        P,
        "get_settings",
        lambda: SimpleNamespace(BROWSER_BRIDGE_URL="http://b", TWITTER_LOGIN_USERNAME="u", TWITTER_LOGIN_EMAIL="", TWITTER_LOGIN_PASSWORD="pw"),
    )
    r = await P._publish_twitter_via_browser(_account("twitter", username="cu_dev"), "hi", _post(), [])
    assert not r.success and r.skipped

    # login returns non-logged_in
    client2 = _bridge_client(
        is_twitter_logged_in=AsyncMock(return_value={"logged_in": False}), twitter_login=AsyncMock(return_value={"status": "error", "error": "2fa"})
    )
    _patch_browser(monkeypatch, client2)
    r2 = await P._publish_twitter_via_browser(_account("twitter", username="cu_dev"), "hi", _post(), [])
    assert not r2.success and r2.skipped and "2fa" in r2.error

    # post_tweet non-ok
    client3 = _bridge_client(post_tweet=AsyncMock(return_value={"status": "error", "error": "composer dead"}))
    _patch_browser(monkeypatch, client3)
    r3 = await P._publish_twitter_via_browser(_account("twitter", username="cu_dev"), "hi", _post(), [])
    assert not r3.success and "composer dead" in r3.error


# ── instagram sub-paths ───────────────────────────────────────────────


def test_sidecar_file_path_matrix():
    assert P._sidecar_file_path("/app/uploads/2024/01/i.jpg") == "/uploads/2024/01/i.jpg"
    assert P._sidecar_file_path("/uploads/x.jpg") == "/uploads/x.jpg"
    assert P._sidecar_file_path("uploads/x.jpg") == "/uploads/x.jpg"
    assert P._sidecar_file_path("rel/x.jpg") == "/uploads/rel/x.jpg"
    assert P._sidecar_file_path("/other/x.jpg") is None
    assert P._sidecar_file_path("") is None


@pytest.mark.asyncio
async def test_ig_public_urls(monkeypatch):
    # manual override wins
    post = _post(platform_specific={"instagram": {"image_urls": ["https://o/1"]}})
    assert await P._instagram_public_urls(["sp"], post) == ["https://o/1"]
    post2 = _post(platform_specific={"instagram": {"image_url": "https://o/one"}})
    assert await P._instagram_public_urls(["sp"], post2) == ["https://o/one"]

    # r2-backed jpeg asset → r2.dev url; else /media/view jpeg
    asset = SimpleNamespace(storage_path="p/a.png", storage_backend="r2", mime_type="image/png")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [asset]))))
    monkeypatch.setattr(P._settings, "R2_PUBLIC_URL", "https://pub.r2.dev/")
    monkeypatch.setattr(P, "_media_public_url", lambda sp, force_jpeg=False: f"mv/{sp}")
    post3 = _post(media_ids=[uuid.uuid4()])
    urls = await P._instagram_public_urls(["p/a.png", "p/b.webp"], post3, db)
    assert urls[0] == "https://pub.r2.dev/p/a.png"  # r2 fast path
    assert urls[1] == "mv/p/b.webp"  # media/view fallback


@pytest.mark.asyncio
async def test_ig_sidecar_guards(monkeypatch):
    r = await P._publish_instagram_via_sidecar(_account("instagram"), "t", _post(), [])
    assert r.skipped
    r2 = await P._publish_instagram_via_sidecar(_account("instagram", meta_data={}), "t", _post(), ["p.png"])
    assert "session not found" in r2.error


@pytest.mark.asyncio
async def test_ig_sidecar_uploads(monkeypatch):
    calls = []

    class _PClient:
        def __init__(self, url):
            pass

        async def login_by_sessionid(self, session_id, proxy=None):
            calls.append(("login", proxy))

        async def upload_photo(self, session_id, file_path, caption):
            calls.append(("photo", file_path))
            return {"id": "m1", "code": "C1"}

        async def upload_video(self, session_id, file_path, caption):
            calls.append(("video", file_path))
            return {"pk": "m2"}

        async def upload_album(self, session_id, file_paths, caption):
            calls.append(("album", len(file_paths)))
            return {"id": "m3"}

    monkeypatch.setattr(P, "InstagramPrivateAPIClient", _PClient)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P._settings, "INSTAGRAM_PROXY", "http://warp")
    acc = _account("instagram", meta_data={"private_api_session_id": "sid"})

    r = await P._publish_instagram_via_sidecar(acc, "t", _post(), ["a.png"])
    assert r.success and r.platform_url == "https://www.instagram.com/p/C1/"
    assert calls[0] == ("login", "http://warp") and calls[1] == ("photo", "a.png")

    r2 = await P._publish_instagram_via_sidecar(acc, "t", _post(), ["v.mp4"])
    assert r2.platform_post_id == "m2"

    await P._publish_instagram_via_sidecar(acc, "t", _post(), ["a.png", "b.png"])
    assert calls[-1] == ("album", 2)


@pytest.mark.asyncio
async def test_ig_sidecar_session_expired(monkeypatch):
    class _PClient:
        def __init__(self, url):
            pass

        async def login_by_sessionid(self, **kw):
            pass

        async def upload_photo(self, **kw):
            raise P.InstagramPrivateAPIError(401, "login_required")

    monkeypatch.setattr(P, "InstagramPrivateAPIClient", _PClient)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    acc = _account("instagram", meta_data={"private_api_session_id": "sid"})
    r = await P._publish_instagram_via_sidecar(acc, "t", _post(), ["a.png"])
    assert not r.success and "session expired" in r.error


@pytest.mark.asyncio
async def test_ig_find_live():
    client = SimpleNamespace(
        list_recent_media=AsyncMock(
            return_value=[
                {"caption": "hello world", "timestamp": datetime.now(UTC).isoformat()},
                {"caption": "other", "timestamp": datetime.now(UTC).isoformat()},
                {"caption": "hello world", "timestamp": "2020-01-01T00:00:00Z"},
                {"caption": "hello world", "timestamp": "garbage"},
            ]
        )
    )
    live = await P._instagram_find_live(client, "  hello   world  ")
    assert live["caption"] == "hello world"
    assert await P._instagram_find_live(client, "") is None
    client2 = SimpleNamespace(list_recent_media=AsyncMock(side_effect=Exception("x")))
    assert await P._instagram_find_live(client2, "cap") is None


@pytest.mark.asyncio
async def test_ig_graph_person_probe_skip(monkeypatch):
    resp = SimpleNamespace(json=lambda: {"account_type": "PERSONAL"})

    class _Http:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return resp

    monkeypatch.setattr(P.httpx, "AsyncClient", _Http)
    acc = _account("instagram", meta_data={"account_type": "person"})
    r = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert r.skipped and "Business or Creator" in r.error


@pytest.mark.asyncio
async def test_ig_graph_no_urls_skip(monkeypatch):
    monkeypatch.setattr(P, "_instagram_public_urls", AsyncMock(return_value=[]))
    acc = _account("instagram", meta_data={"ig_business_id": "b1"})
    r = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert r.skipped and "publicly accessible" in r.error
    r2 = await P._publish_instagram_via_graph("t", "hi", acc, _post(), [], [])
    assert r2.skipped and "at least one image" in r2.error


@pytest.mark.asyncio
async def test_ig_graph_single_and_carousel(monkeypatch):
    monkeypatch.setattr(P, "_instagram_public_urls", AsyncMock(return_value=["u1"]))
    calls = []

    class _IG:
        def __init__(self, **kw):
            pass

        async def create_image_container(self, image_url, caption):
            calls.append(("img", image_url))
            return "cid"

        async def create_carousel_item(self, image_url):
            calls.append(("item", image_url))
            return f"c-{image_url}"

        async def create_carousel_container(self, children_ids, caption):
            calls.append(("carousel", children_ids))
            return "cid"

        async def wait_for_container_ready(self, cid, timeout):
            pass

        async def publish_container(self, cid):
            return "m-live"

    monkeypatch.setattr(P, "InstagramAPIClient", _IG)
    acc = _account("instagram", meta_data={"ig_business_id": "b1"})
    r = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert r.success and r.platform_post_id == "m-live"
    assert calls[0] == ("img", "u1")

    calls.clear()
    monkeypatch.setattr(P, "_instagram_public_urls", AsyncMock(return_value=["u1", "u2"]))
    r2 = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert r2.success and calls[-1][0] == "carousel"


@pytest.mark.asyncio
async def test_ig_graph_publish_error_ambiguous_then_live(monkeypatch):
    monkeypatch.setattr(P, "_instagram_public_urls", AsyncMock(return_value=["u1"]))
    monkeypatch.setattr(P.asyncio, "sleep", AsyncMock())

    class _Err(P.InstagramAPIError):
        pass

    class _IG:
        def __init__(self, **kw):
            pass

        async def create_image_container(self, image_url, caption):
            return "cid"

        async def wait_for_container_ready(self, cid, timeout):
            pass

        async def publish_container(self, cid):
            raise _Err(400, "limit", "u")

    monkeypatch.setattr(P, "InstagramAPIClient", _IG)

    # post went live despite the error → idempotent success
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value={"id": "L1", "permalink": "https://ig/L1"}))
    acc = _account("instagram", meta_data={"ig_business_id": "b1"})
    r = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert r.success and r.platform_post_id == "L1"

    # not live → ambiguous failure (reconcile later)
    monkeypatch.setattr(P, "_instagram_find_live", AsyncMock(return_value=None))
    r2 = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert not r2.success and r2.ambiguous

    # error before publish_attempted → not ambiguous, no find_live poll
    class _IG2(_IG):
        async def create_image_container(self, image_url, caption):
            raise _Err(400, "bad", "u")

    monkeypatch.setattr(P, "InstagramAPIClient", _IG2)
    r3 = await P._publish_instagram_via_graph("t", "hi", acc, _post(), ["p"], ["p"])
    assert not r3.success and not r3.ambiguous


@pytest.mark.asyncio
async def test_ig_instagrapi_paths(monkeypatch):
    r = await P._publish_instagram_via_instagrapi("t", _post(), [])
    assert r.skipped

    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="", INSTAGRAM_PASSWORD="", INSTAGRAM_PROXY=""))
    r2 = await P._publish_instagram_via_instagrapi("t", _post(), ["p.png"])
    assert "not set" in r2.error

    calls = []

    class _IC:
        def __init__(self, username, password, proxy=None):
            calls.append(("init", username, proxy))

        async def upload_photo(self, fp, caption):
            calls.append(("photo", fp))
            return {"id": "i1", "code": "CC"}

        async def upload_video(self, fp, caption):
            calls.append(("video", fp))
            return {"pk": "i2"}

        async def upload_album(self, fps, caption):
            calls.append(("album", len(fps)))
            return {"id": "i3"}

    monkeypatch.setattr(P, "InstagrapiClient", _IC)
    monkeypatch.setattr(P, "_settings", SimpleNamespace(INSTAGRAM_USERNAME="u", INSTAGRAM_PASSWORD="p", INSTAGRAM_PROXY="px"))
    r3 = await P._publish_instagram_via_instagrapi("t", _post(), ["a.png"])
    assert r3.success and r3.platform_url == "https://www.instagram.com/p/CC/"
    assert calls[0] == ("init", "u", "px")
    await P._publish_instagram_via_instagrapi("t", _post(), ["v.mp4"])
    await P._publish_instagram_via_instagrapi("t", _post(), ["a.png", "b.png"])
    assert calls[3] == ("video", "v.mp4") and calls[5] == ("album", 2)


@pytest.mark.asyncio
async def test_ig_resolve_user_token(monkeypatch):
    # business_login → passthrough
    acc = _account("instagram", meta_data={"login_type": "business_login"})
    assert await P._resolve_ig_user_token("tok", acc, None) == "tok"

    # parent FB user → parent token
    parent = SimpleNamespace(platform="facebook", account_type="user", access_token_enc=b"ptok")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: parent)))
    monkeypatch.setattr(P, "decrypt_token", lambda v: "dec-" + v.decode())
    acc2 = _account("instagram", meta_data={})
    acc2.parent_account_id = uuid.uuid4()
    acc2.team_id = uuid.uuid4()
    assert await P._resolve_ig_user_token("tok", acc2, db) == "dec-ptok"

    # no parent + db finds team fb user → its token
    acc3 = _account("instagram", meta_data={})
    acc3.parent_account_id = None
    fb_user = SimpleNamespace(access_token_enc=b"teamtok")
    db2 = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: fb_user))))
    assert await P._resolve_ig_user_token("tok", acc3, db2) == "dec-teamtok"

    # no db → stored token
    assert await P._resolve_ig_user_token("tok", acc3, None) == "tok"


# ── facebook sidecar + page-token ─────────────────────────────────────


def _fb_sidecar(**over):
    base = dict(
        set_session=AsyncMock(return_value={}),
        post_text=AsyncMock(return_value={"url": "https://fb/t1", "post_id": "t1"}),
        post_link=AsyncMock(return_value={"url": "https://fb/l1"}),
        post_photo=AsyncMock(return_value={"post_id": "ph1", "url": "https://fb/ph1"}),
        post_video=AsyncMock(return_value={"post_id": "v1"}),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_fbs_no_session():
    r = await P._publish_facebook_via_sidecar(_account("facebook", meta_data={}), "hi", _post(), [])
    assert "browser session" in r.error


@pytest.mark.asyncio
async def test_fbs_post_types(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00" * 12000)
    client = _fb_sidecar()
    monkeypatch.setattr(P, "FacebookSidecarClient", lambda: client)
    acc = _account("facebook", meta_data={"browser_storage_state": {"s": 1}})

    r = await P._publish_facebook_via_sidecar(acc, "hi", _post(), [])
    assert r.success and r.platform_post_id == "t1"
    assert client.post_text.await_args.kwargs["privacy"] == "public"

    r2 = await P._publish_facebook_via_sidecar(acc, "hi", _post(link_url="https://x.io"), [])
    assert r2.success and r2.platform_url == "https://fb/l1"

    r3 = await P._publish_facebook_via_sidecar(acc, "hi", _post(), [str(img)])
    assert r3.platform_post_id == "ph1"
    imgs = client.post_photo.await_args.kwargs["images"]
    assert imgs[0]["filename"] == "p.png"

    r4 = await P._publish_facebook_via_sidecar(acc, "hi", _post(), [str(vid)])
    assert r4.platform_post_id == "v1"

    # privacy override
    await P._publish_facebook_via_sidecar(acc, "hi", _post(platform_specific={"facebook_privacy": "friends"}), [])
    assert client.post_text.await_args.kwargs["privacy"] == "friends"


@pytest.mark.asyncio
async def test_fbs_no_id_and_error(monkeypatch):
    client = _fb_sidecar(post_text=AsyncMock(return_value={}))
    monkeypatch.setattr(P, "FacebookSidecarClient", lambda: client)
    acc = _account("facebook", meta_data={"browser_storage_state": {"s": 1}})
    r = await P._publish_facebook_via_sidecar(acc, "hi", _post(), [])
    assert not r.success and "cannot confirm" in r.error

    client2 = _fb_sidecar(set_session=AsyncMock(side_effect=P.FacebookSidecarError(401, "expired")))
    monkeypatch.setattr(P, "FacebookSidecarClient", lambda: client2)
    r2 = await P._publish_facebook_via_sidecar(acc, "hi", _post(), [])
    assert r2.error == "expired"


@pytest.mark.asyncio
async def test_fb_page_token_cached_and_lookup(monkeypatch):
    resp_accounts = SimpleNamespace(
        status_code=200,
        json=lambda: {"data": [{"id": "pg1", "access_token": "PAGE_TOK"}]},
    )

    class _Http:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return resp_accounts

    monkeypatch.setattr(P.httpx, "AsyncClient", _Http)
    tok = await P._facebook_page_token("usertok", "pg1")
    assert tok == "PAGE_TOK"


# ── instagram web API (rupload) path ──────────────────────────────────


class _IGWebHttp:
    """Fake httpx.AsyncClient recording calls, routing by URL substring."""

    responses: dict = {}
    calls: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        _IGWebHttp.calls.append(url)
        for key, (status, payload) in _IGWebHttp.responses.items():
            if key in url:
                return SimpleNamespace(status_code=status, text=str(payload), json=lambda: payload)
        return SimpleNamespace(status_code=500, text="unrouted", json=lambda: {})


def _ig_web_account():
    return _account(
        "instagram",
        meta_data={
            "private_api_session_id": "sid",
            "private_api_csrf_token": "csrf",
            "private_api_ds_user_id": "uid",
        },
    )


@pytest.mark.asyncio
async def test_igweb_session_incomplete():
    acc = _account("instagram", meta_data={"private_api_session_id": "s"})
    r = await P._publish_instagram_via_web(acc, "hi", _post(), ["p.png"])
    assert "session incomplete" in r.error


@pytest.mark.asyncio
async def test_igweb_rejects_video(tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00" * 12000)
    monkey = _ig_web_account()
    r = await P._publish_instagram_via_web(monkey, "hi", _post(), [str(vid)])
    assert "not yet supported" in r.error


@pytest.mark.asyncio
async def test_igweb_single_photo_happy(monkeypatch, tmp_path):
    from PIL import Image

    img = tmp_path / "p.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(img)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P.httpx, "AsyncClient", _IGWebHttp)
    _IGWebHttp.calls = []
    _IGWebHttp.responses = {
        "rupload_igphoto": (200, {"status": "ok"}),
        "media/configure": (200, {"media": {"id": "ig1", "code": "CXY"}}),
    }
    r = await P._publish_instagram_via_web(_ig_web_account(), "hi", _post(), [str(img)])
    assert r.success and r.platform_post_id == "ig1"
    assert r.platform_url == "https://www.instagram.com/p/CXY/"
    assert any("rupload_igphoto" in u for u in _IGWebHttp.calls)
    assert any("media/configure/" in u for u in _IGWebHttp.calls)
    assert not any("configure_sidecar" in u for u in _IGWebHttp.calls)


@pytest.mark.asyncio
async def test_igweb_carousel_happy(monkeypatch, tmp_path):
    from PIL import Image

    i1 = tmp_path / "a.png"
    i2 = tmp_path / "b.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(i1)
    Image.new("RGB", (100, 100), (0, 255, 0)).save(i2)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P.httpx, "AsyncClient", _IGWebHttp)
    _IGWebHttp.calls = []
    _IGWebHttp.responses = {
        "rupload_igphoto": (200, {"status": "ok"}),
        "configure_sidecar": (200, {"media": {"id": "alb9", "code": "CS"}}),
    }
    r = await P._publish_instagram_via_web(_ig_web_account(), "hi", _post(), [str(i1), str(i2)])
    assert r.success and r.platform_post_id == "alb9"
    assert sum("rupload_igphoto" in u for u in _IGWebHttp.calls) == 2
    assert any("configure_sidecar" in u for u in _IGWebHttp.calls)


@pytest.mark.asyncio
async def test_igweb_upload_and_configure_failures(monkeypatch, tmp_path):
    from PIL import Image

    img = tmp_path / "p.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(img)
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P.httpx, "AsyncClient", _IGWebHttp)

    # rupload non-200
    _IGWebHttp.calls = []
    _IGWebHttp.responses = {"rupload_igphoto": (500, {"err": 1})}
    r = await P._publish_instagram_via_web(_ig_web_account(), "hi", _post(), [str(img)])
    assert not r.success and "rupload 1 failed" in r.error

    # rupload status not ok
    _IGWebHttp.responses = {"rupload_igphoto": (200, {"status": "fail"})}
    r2 = await P._publish_instagram_via_web(_ig_web_account(), "hi", _post(), [str(img)])
    assert "status not ok" in r2.error

    # configure non-200
    _IGWebHttp.responses = {
        "rupload_igphoto": (200, {"status": "ok"}),
        "media/configure": (400, {"err": "bad configure"}),
    }
    r3 = await P._publish_instagram_via_web(_ig_web_account(), "hi", _post(), [str(img)])
    assert "configure failed" in r3.error
