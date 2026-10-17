"""Remaining reachable branch arms in app/services/publishing.py.

Covers:
- music mixing for an image post, and a failed mix that leaves the path
  untouched
- absolute or local-backend media/music paths that are missing on disk
- X text trimming/threading edge shapes
- a minimal OAuth2 refresh response
- the browser publish path with no login credentials configured
- an FB photo upload that returns no id
- an IG live match that's too old
- an IG parent that is a Facebook page, not a user
- a TikTok poll timeout with no uploaded-byte count
- a Threads asset with no public URL

Reuses the fakes from the existing publishing suites; every platform,
the bridge, storage and ffmpeg are faked."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import publishing as P
from tests.unit.test_publishing_flow import (  # noqa: F401
    _account,
    _base_patches,
    _bridge_client,
    _FBHttp,
    _patch_browser,
    _post,
)
from tests.unit.test_publishing_helpers import _DB


@pytest.mark.asyncio
@pytest.mark.usefixtures("_base_patches")
async def test_music_not_mixed_into_image_post(monkeypatch):
    monkeypatch.setattr(P, "_resolve_media_paths", AsyncMock(return_value=["/a.jpg"]))
    monkeypatch.setattr(P, "_resolve_music_path", AsyncMock(return_value="/a.mp3"))
    mix = AsyncMock()
    monkeypatch.setattr(P, "_mix_audio_into_video", mix)
    fn = AsyncMock(return_value=P.PublishResult(success=True))
    monkeypatch.setattr(P, "_publish_twitter", fn)
    r = await P.publish_to_platform(_account("twitter"), _post(media_ids=[uuid.uuid4()]), None)
    assert r.success and fn.await_args.args[4] == ["/a.jpg"]
    mix.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_base_patches")
async def test_failed_mix_keeps_original_video(monkeypatch):
    monkeypatch.setattr(P, "_resolve_media_paths", AsyncMock(return_value=["/v.mp4"]))
    monkeypatch.setattr(P, "_resolve_music_path", AsyncMock(return_value="/a.mp3"))
    monkeypatch.setattr(P, "_mix_audio_into_video", AsyncMock(side_effect=lambda v, m: v))
    fn = AsyncMock(return_value=P.PublishResult(success=True))
    monkeypatch.setattr(P, "_publish_twitter", fn)
    await P.publish_to_platform(_account("twitter"), _post(media_ids=[uuid.uuid4()]), None)
    assert fn.await_args.args[4] == ["/v.mp4"]


@pytest.mark.asyncio
async def test_media_absolute_path_and_local_backend_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    present = tmp_path / "abs.png"
    present.write_bytes(b"x")
    a = SimpleNamespace(id=uuid.uuid4(), storage_path=str(present), filename="abs.png", storage_backend="local")
    gone = SimpleNamespace(id=uuid.uuid4(), storage_path="gone.png", filename="gone.png", storage_backend="local")
    out = await P._resolve_media_paths(SimpleNamespace(media_ids=[a.id, gone.id]), _DB(results=[[a, gone]]))
    assert out == [str(present)]  # absolute path used as-is; missing local file dropped, no remote fetch


@pytest.mark.asyncio
async def test_music_absolute_path_and_local_backend_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    song = tmp_path / "song.mp3"
    song.write_bytes(b"mp3")
    a = SimpleNamespace(id=uuid.uuid4(), storage_path=str(song), filename="song.mp3", storage_backend="local")
    assert await P._resolve_music_path(SimpleNamespace(music_asset_id=a.id), _DB(results=[a])) == str(song)
    gone = SimpleNamespace(id=uuid.uuid4(), storage_path="gone.mp3", filename="gone.mp3", storage_backend="local")
    assert await P._resolve_music_path(SimpleNamespace(music_asset_id=gone.id), _DB(results=[gone])) is None


def test_fit_x_limit_whitespace_heavy_text_keeps_all_words():
    text = "hello" + " " * 300 + "world"
    assert P._x_weighted_len(text) > 280
    assert P._fit_x_limit(text) == "hello world"


def test_split_thread_oversized_word_and_blank_text():
    giant = "x" * 300
    assert P._split_thread(f"{giant} tail") == [giant, "tail"]
    assert P._split_thread(" " * 400) == []


@pytest.mark.asyncio
async def test_refresh_minimal_response_keeps_existing_fields(monkeypatch):
    class _TC:
        async def refresh_token(self, rt):
            return {"access_token": "new", "scope": "   "}

    import app.api.auth as authmod

    monkeypatch.setattr(authmod, "twitter_client", _TC())
    monkeypatch.setattr(P, "decrypt_token", lambda t: "rt")
    expiry = datetime.now(UTC) + timedelta(hours=1)
    acct = SimpleNamespace(refresh_token_enc=b"old-rt", access_token_enc=None, token_expires_at=expiry,
                           scopes=["tweet.write"], status="expired")
    db = _DB()
    db.commit = AsyncMock()
    assert await P._refresh_oauth2_token(acct, db) == "new"
    assert acct.refresh_token_enc == b"old-rt" and acct.token_expires_at == expiry
    assert acct.scopes == ["tweet.write"] and acct.status == "active"


@pytest.mark.asyncio
async def test_browser_publish_without_login_credentials(monkeypatch, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    client = _bridge_client(
        is_twitter_logged_in=AsyncMock(return_value={"logged_in": False}),
        post_tweet=AsyncMock(return_value={"status": "error", "error": "login wall"}),
    )
    _patch_browser(monkeypatch, client)
    s = P.get_settings()
    monkeypatch.setattr(s, "TWITTER_LOGIN_PASSWORD", "", raising=False)
    r = await P._publish_twitter_via_browser(_account("twitter", username="cu_dev"), "hi", _post(), [str(img)])
    assert not r.success and "login wall" in r.error
    client.twitter_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_fb_photo_upload_without_id_refuses_to_publish(monkeypatch, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for f in (a, b):
        f.write_bytes(b"\x89PNG" + b"\x00" * 12000)
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda acc: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    create_post = AsyncMock(return_value={"id": "txt"})
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: SimpleNamespace(create_post=create_post))
    monkeypatch.setattr(P.httpx, "AsyncClient", _FBHttp)
    base = f"{P.FACEBOOK_GRAPH_BASE}/{P.FACEBOOK_GRAPH_VERSION}/pg1"
    monkeypatch.setattr(_FBHttp, "posts", [])
    monkeypatch.setattr(_FBHttp, "responses", {f"{base}/photos": (200, {}), f"{base}/feed": (200, {"id": "alb"})})
    r = await P._publish_facebook("t", "hi", _account("facebook", account_id="pg1"), _post(), [str(a), str(b)], None)
    # Owner rule: id-less uploads → fail; never a text-only post, never an album.
    assert not r.success and "0/2" in r.error and "missing media" in r.error
    create_post.assert_not_awaited()
    assert not any(u.endswith("/feed") for u, *_ in _FBHttp.posts)


@pytest.mark.asyncio
async def test_instagram_find_live_ignores_stale_match():
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    client = SimpleNamespace(list_recent_media=AsyncMock(return_value=[{"id": "1", "caption": "cap", "timestamp": old}]))
    assert await P._instagram_find_live(client, "cap") is None


@pytest.mark.asyncio
async def test_ig_token_parent_is_facebook_page_falls_to_team_lookup():
    parent = SimpleNamespace(platform="facebook", account_type="page", access_token_enc=b"enc")
    account = SimpleNamespace(meta_data={}, parent_account_id=uuid.uuid4(), team_id=uuid.uuid4())
    db = _DB(results=[parent, None])
    assert await P._resolve_ig_user_token("stored-tok", account, db) == "stored-tok"
    assert db._q == []  # both the parent and the team lookup ran


@pytest.mark.asyncio
async def test_tiktok_poll_timeout_without_uploaded_bytes():
    client = SimpleNamespace(check_publish_status=AsyncMock(return_value={"data": {"status": "PROCESSING_UPLOAD"}}))
    r = await P._poll_tiktok_publish_status(client, "pub1", "DIRECT_POST", "me", attempts=2, interval_sec=0)
    assert not r.success and "publish timeout" in r.error
    assert "last_status=PROCESSING_UPLOAD" in r.error and "uploaded_bytes" not in r.error
    assert r.platform_meta == {"tiktok": {"publish_id": "pub1", "publish_mode": "DIRECT_POST", "status": "PROCESSING_UPLOAD"}}


@pytest.mark.asyncio
async def test_threads_refuses_when_an_asset_has_no_public_url(monkeypatch):
    from unittest.mock import patch

    from tests.unit.test_publishing import _FakeAsyncClient

    monkeypatch.setattr(P, "_media_public_url", lambda sp, **kw: None if "private" in sp else f"https://cdn/{sp}")
    fake = _FakeAsyncClient([])
    acct = SimpleNamespace(account_id="u1", username="me")
    with patch("app.services.threads_api.httpx.AsyncClient", new=lambda timeout=60.0: fake):
        r = await P._publish_threads("tok", "hello", acct, SimpleNamespace(), ["/a.png", "/b.png"], ["private/a.png", "pub/b.png"])
        # None resolve → still refused (no text-only fallback).
        monkeypatch.setattr(P, "_media_public_url", lambda sp, **kw: None)
        r2 = await P._publish_threads("tok", "hello", acct, SimpleNamespace(), ["/a.png"], ["pub/a.png"])
    assert not r.success and r.skipped and "1/2" in r.error
    assert not r2.success and r2.skipped and "0/1" in r2.error
    assert fake.calls == []  # no container (text or image) was ever created
