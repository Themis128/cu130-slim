"""Tail coverage round 3 — the last uncovered branches across x_web,
publishing (telegram dispatch + instagrapi edges), brand API, telegram
webhook paths, linkedin_ads_report, tiktok_api, analytics_sync,
email_digest, messenger tasks, and misc service stragglers."""

from __future__ import annotations

import io
import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

TEAM_ID = uuid.uuid4()


class _Res:
    def __init__(self, first=None, all=None, scalar=None):
        self._first = first
        self._all = all if all is not None else ([first] if first else [])
        self._scalar = scalar

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._first


class _DB:
    def __init__(self, results=None, team=None):
        self._q = list(results or [])
        self._team = team
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, key):
        return self._team

    async def execute(self, *a, **k):
        return self._q.pop(0) if self._q else _Res()

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, o):
        pass


def _team():
    return SimpleNamespace(id=TEAM_ID)


# ── x_web: media re-encode edges ────────────────────────────────────────────


def _png_bytes(mode="RGB", size=(8, 8)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size).save(buf, "PNG")
    return buf.getvalue()


def test_to_jpeg_converts_palette_mode(tmp_path):
    from app.services import x_web

    src = tmp_path / "p.png"
    src.write_bytes(_png_bytes("P"))
    out = x_web._to_jpeg(str(src), str(tmp_path), 0)
    assert out.endswith(".jpg")


def test_to_jpeg_raises_when_never_small_enough(tmp_path, monkeypatch):
    from app.services import x_web

    src = tmp_path / "big.png"
    src.write_bytes(_png_bytes())
    monkeypatch.setattr(os.path, "getsize", lambda p: 99 * 1024 * 1024)
    with pytest.raises(x_web.XWebMediaError):
        x_web._to_jpeg(str(src), str(tmp_path), 0)


def test_prepare_media_wraps_unexpected_error(tmp_path, monkeypatch):
    from app.services import x_web

    src = tmp_path / "a.png"
    src.write_bytes(_png_bytes())
    plan = x_web.plan_media([str(src)])
    monkeypatch.setattr(x_web, "_to_jpeg", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    with pytest.raises(x_web.XWebMediaError):
        x_web.prepare_media(plan, str(tmp_path))


# ── x_web: tweety patch internals ────────────────────────────────────────────


def test_patch_tweety_returns_early_without_tweety(monkeypatch):
    from app.services import x_web

    monkeypatch.setitem(sys.modules, "tweety.transaction", None)
    x_web._patch_tweety_home_fallback()


@pytest.mark.asyncio
async def test_patch_tweety_i_jf_fallback():
    from tweety.http import Request

    from app.services import x_web

    manifest = '{1234:"ondemand.s"} 1234:"deadbeef"'
    orig_saved = Request.get_home_html

    async def fake_home(self):
        return "<html>no manifest</html>"

    Request.get_home_html = fake_home
    try:
        x_web._patch_tweety_home_fallback()
        x_web._patch_tweety_home_fallback()  # already-patched early return

        resp = SimpleNamespace(status_code=200, content=manifest.encode())
        fake_self = SimpleNamespace(
            _get_request_headers=lambda: {"authorization": "x", "ua": "y"},
            _session=SimpleNamespace(request=AsyncMock(return_value=resp)),
        )
        out = await Request.get_home_html(fake_self)
        assert "deadbeef" in str(out)
        call_headers = fake_self._session.request.await_args.kwargs["headers"]
        assert "authorization" not in call_headers
    finally:
        Request.get_home_html = orig_saved


# ── x_web: publish error paths ───────────────────────────────────────────────


def _xw_settings(**over):
    base = dict(
        X_WEB_FALLBACK_ENABLED=True,
        X_WEB_AUTH_TOKEN="tok",
        X_WEB_CT0="csrf",
        X_WEB_COOKIES_JSON="",
        X_WEB_PROXY="",
        X_WEB_MAX_POSTS_PER_DAY=50,
        X_WEB_MIN_GAP_MINUTES=0,
        X_WEB_MAX_GAP_MINUTES=0,
        X_WEB_BREAKER_HOURS=6.0,
        X_WEB_ANALYTICS_MIN_INTERVAL_HOURS=0.0,
        X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS=10,
        X_WEB_STATE_FILE="/tmp/x_web_tail3_state.json",
        REDIS_URL="redis://invalid:1/0",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture()
def xw_settings(monkeypatch):
    from app.services import x_web

    s = _xw_settings()
    monkeypatch.setattr(x_web, "get_settings", lambda: s)
    return s


def _xw_guard():
    from app.services import x_web

    return x_web.XWebGuard(x_web.MemoryGuardStore())


class _NoidTweety:
    def __init__(self, fail_at=None):
        self.user = SimpleNamespace(username="tbaltzakis")
        self.calls = 0
        self._fail_at = fail_at

    async def create_tweet(self, text, files=None, reply_to=None):
        self.calls += 1
        if self._fail_at is not None and self.calls == self._fail_at:
            return SimpleNamespace()  # no id attribute
        return SimpleNamespace(id=str(2000 + self.calls))

    async def get_tweets(self, ident, pages=1):
        return SimpleNamespace(tweets=[])


class _UploadFailTweety:
    def __init__(self):
        self.user = SimpleNamespace(username="tbaltzakis")

    async def upload_media(self, files):
        raise RuntimeError("socket blew up mid-upload")


def _client_factory(client):
    async def make():
        return client

    return make


@pytest.mark.asyncio
async def test_x_web_head_tweet_no_id_is_ambiguous(xw_settings):
    from app.services import x_web

    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis",
        chunks=["hi"],
        media_paths=None,
        guard=_xw_guard(),
        client_factory=_client_factory(_NoidTweety(fail_at=1)),
        sleep=AsyncMock(),
    )
    assert out.status == "ambiguous"


@pytest.mark.asyncio
async def test_x_web_thread_reply_no_id_returns_partial(xw_settings):
    from app.services import x_web

    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis",
        chunks=["one", "two"],
        media_paths=None,
        guard=_xw_guard(),
        client_factory=_client_factory(_NoidTweety(fail_at=2)),
        sleep=AsyncMock(),
    )
    assert out.status == "ok" and out.partial and out.tweet_ids == ["2001"]


@pytest.mark.asyncio
async def test_x_web_media_stage_error_is_media_error(tmp_path, xw_settings):
    from app.services import x_web

    img = tmp_path / "a.jpg"
    buf = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (8, 8)).save(buf, "JPEG")
    img.write_bytes(buf.getvalue())

    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis",
        chunks=["hi"],
        media_paths=[str(img)],
        guard=_xw_guard(),
        client_factory=_client_factory(_UploadFailTweety()),
        sleep=AsyncMock(),
    )
    assert out.status == "media_error"


# ── x_web: twscrape fallback ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_twscrape_api_builds_pool(monkeypatch, xw_settings):
    from app.services import x_web

    captured = {}

    class _Pool:
        async def add_account_cookies(self, username, cookie_header):
            captured["username"] = username
            captured["cookies"] = cookie_header

    class _FakeAPI:
        def __init__(self, db_path, proxy=None, raise_when_no_account=False):
            captured["db_path"] = db_path
            captured["raise"] = raise_when_no_account
            self.pool = _Pool()

    monkeypatch.setattr("twscrape.API", _FakeAPI)
    api = await x_web._default_twscrape_api("tbaltzakis")
    assert isinstance(api, _FakeAPI)
    assert captured["username"] == "tbaltzakis"
    assert "auth_token=tok" in captured["cookies"]
    assert captured["raise"] is True
    assert os.path.isdir(api._x_web_workdir)


@pytest.mark.asyncio
async def test_fetch_x_web_analytics_twscrape_trip(xw_settings):
    from app.services import x_web

    async def tweety_fail():
        raise RuntimeError("plain boom")

    async def twscrape_fail(username):
        raise RuntimeError("account suspended by X")

    guard = _xw_guard()
    result, reason = await x_web.fetch_x_web_analytics(
        username="tbaltzakis",
        user_id=None,
        guard=guard,
        tweety_factory=tweety_fail,
        twscrape_factory=twscrape_fail,
    )
    assert result is None and "tripped breaker" in reason


# ── publishing: telegram dispatch variants ───────────────────────────────────


def _tg_account():
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=TEAM_ID,
        platform="telegram",
        access_token_enc=b"enc",
        username="cloudless",
        account_id="tg",
        account_type="channel",
        meta_data={"telegram_channel": {"chat_id": "-1001", "title": "HQ"}},
    )


def _tg_post(**kw):
    kw.setdefault("media_ids", [])
    kw.setdefault("link_url", None)
    kw.setdefault("link_preview_override", None)
    kw.setdefault("title", None)
    kw.setdefault("platform_specific", {})
    kw.setdefault("hashtags", [])
    kw.setdefault("music_asset_id", None)
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=TEAM_ID,
        content_text="hello",
        scheduled_at=None,
        status="draft",
        **kw,
    )


def _tg_db():
    return SimpleNamespace(add=lambda *a: None, commit=AsyncMock(), rollback=AsyncMock())


def _mkmedia(tmp_path, names):
    """Create real media files so the existence/size validator passes."""
    from PIL import Image

    media, storage = [], []
    for name in names:
        p = tmp_path / name
        if name.endswith(".pdf"):
            p.write_bytes(b"%PDF-1.4 fake\n" + b"\x00" * 12000)
        elif name.endswith((".mp4", ".mov")):
            p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12000)
        else:
            # A noisy PNG well above MIN_IMAGE_BYTES (10 KB)
            img = Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            p.write_bytes(buf.getvalue())
        media.append(str(p))
        storage.append(f"u/{name}")
    return media, storage


def _base_patches(monkeypatch, *, text="hello", media=None, storage=None, url_map=None, client=None, db=None):
    from app.services import publishing as P

    monkeypatch.setattr(P, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(P, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(P, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(P, "_find_duplicate_post", AsyncMock(return_value=None))
    monkeypatch.setattr(P, "_resolve_media_paths", AsyncMock(return_value=media or []))
    monkeypatch.setattr(P, "_resolve_media_storage_paths", AsyncMock(return_value=storage or []))
    monkeypatch.setattr(P, "_media_public_url", url_map or (lambda sp, **kw: f"https://x/{sp}"))
    monkeypatch.setattr(P, "_build_post_text", lambda post, platform: text)
    if client is not None:
        monkeypatch.setattr(P, "TelegramAPIClient", lambda tok: client)
    return P


@pytest.mark.asyncio
async def test_telegram_text_only_blocked_by_media_gate(monkeypatch):
    send_message = AsyncMock(return_value={"message_id": 7})
    P = _base_patches(monkeypatch, client=SimpleNamespace(send_message=send_message))
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert not r.success and r.skipped and "no media" in r.error.lower()
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_single_photo_dispatch(monkeypatch, tmp_path):
    send_photo = AsyncMock(return_value={"message_id": 7})
    media, storage = _mkmedia(tmp_path, ["p.jpg"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=SimpleNamespace(send_photo=send_photo),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert r.success
    send_photo.assert_awaited_once()
    assert send_photo.await_args.args[0] == "-1001"


@pytest.mark.asyncio
async def test_telegram_album_dispatch(monkeypatch, tmp_path):
    send_media_group = AsyncMock(return_value=[{"message_id": 11}, {"message_id": 12}])
    media, storage = _mkmedia(tmp_path, ["a.jpg", "b.mp4"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=SimpleNamespace(send_media_group=send_media_group),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert r.success
    media = send_media_group.await_args.args[1]
    assert media[0]["type"] == "photo" and media[1]["type"] == "video"
    assert media[0]["caption"] == "hello"


@pytest.mark.asyncio
async def test_telegram_media_without_public_url_skips(monkeypatch, tmp_path):
    media, storage = _mkmedia(tmp_path, ["a.jpg"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        url_map=lambda sp, **kw: None,
        client=SimpleNamespace(),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert not r.success and r.skipped and "public URL" in r.error


@pytest.mark.asyncio
async def test_telegram_single_video_dispatch(monkeypatch, tmp_path):
    send_video = AsyncMock(return_value={"message_id": 9})
    media, storage = _mkmedia(tmp_path, ["v.mp4"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=SimpleNamespace(send_video=send_video),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert r.success
    send_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_single_pdf_dispatch(monkeypatch, tmp_path):
    send_document = AsyncMock(return_value={"message_id": 10})
    media, storage = _mkmedia(tmp_path, ["d.pdf"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=SimpleNamespace(send_document=send_document),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert r.success
    send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_mixed_payload_dispatches_each(monkeypatch, tmp_path):
    client = SimpleNamespace(
        send_document=AsyncMock(return_value={"message_id": 1}),
        send_video=AsyncMock(return_value={"message_id": 2}),
        send_photo=AsyncMock(return_value={"message_id": 3}),
        send_message=AsyncMock(return_value={"message_id": 4}),
    )
    media, storage = _mkmedia(tmp_path, ["a.pdf", "v.mp4", "p.jpg"])
    P = _base_patches(
        monkeypatch,
        text="x" * 2000,
        media=media,
        storage=storage,
        client=client,
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert r.success
    client.send_document.assert_awaited_once()
    client.send_video.assert_awaited_once()
    client.send_photo.assert_awaited_once()
    client.send_message.assert_awaited_once()  # overflow text follows


@pytest.mark.asyncio
async def test_telegram_api_error_maps_to_failure(monkeypatch, tmp_path):
    from app.services.telegram_api import TelegramAPIError

    client = SimpleNamespace(send_photo=AsyncMock(side_effect=TelegramAPIError(400, "chat not found")))
    media, storage = _mkmedia(tmp_path, ["p.jpg"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=client,
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert not r.success and "chat not found" in r.error


@pytest.mark.asyncio
async def test_telegram_event_log_failure_still_succeeds(monkeypatch, tmp_path):
    send_photo = AsyncMock(return_value={"message_id": 8})
    media, storage = _mkmedia(tmp_path, ["p.jpg"])
    P = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=SimpleNamespace(send_photo=send_photo),
    )
    db = SimpleNamespace(
        add=lambda *a: None,
        commit=AsyncMock(side_effect=RuntimeError("db gone")),
        rollback=AsyncMock(),
    )
    r = await P.publish_to_platform(_tg_account(), _tg_post(), db)
    assert r.success
    db.rollback.assert_awaited_once()


# ── publishing: misc edges ───────────────────────────────────────────────────


def test_sidecar_file_path_already_absolute():
    from app.services.publishing import _sidecar_file_path

    assert _sidecar_file_path("/uploads/2024/01/img.jpg") == "/uploads/2024/01/img.jpg"


@pytest.mark.asyncio
async def test_instagrapi_error_maps_to_failure(monkeypatch):
    from app.services import publishing as P

    class _BadClient:
        def __init__(self, **kw):
            pass

        async def upload_photo(self, *a, **k):
            # Raise via publishing's own reference so the except clause catches
            # it even if instagrapi_client was reloaded elsewhere in the suite.
            raise P.InstagrapiError("login_required")

    monkeypatch.setattr(P, "InstagrapiClient", _BadClient)
    monkeypatch.setattr(P._settings, "INSTAGRAM_USERNAME", "u")
    monkeypatch.setattr(P._settings, "INSTAGRAM_PASSWORD", "p")
    monkeypatch.setattr(P._settings, "INSTAGRAM_PROXY", "")
    r = await P._publish_instagram_via_instagrapi("hi", _tg_post(), ["/tmp/a.jpg"])
    assert not r.success and "instagrapi" in r.error


# ── tiktok_api chunk guards + error log ──────────────────────────────────────


def test_video_chunk_plan_too_many_chunks():
    from app.services import tiktok_api

    with pytest.raises(ValueError, match="1000 chunks"):
        tiktok_api._video_chunk_plan((tiktok_api.MAX_CHUNK_COUNT + 1) * tiktok_api.DEFAULT_CHUNK_SIZE)


def test_tiktok_log_api_error_4xx():
    import httpx

    from app.services.tiktok_api import TikTokAPIClient

    client = TikTokAPIClient(access_token="t", open_id="o")
    resp = httpx.Response(400, json={"error": {"message": "bad"}})
    client._log_api_error("https://open.tiktokapis.com/v2/x?token=secret", resp)


# ── email_digest header branches ─────────────────────────────────────────────


def _smtp_settings(**over):
    base = dict(
        SMTP_HOST="localhost",
        SMTP_PORT=25,
        SMTP_USER="",
        SMTP_PASSWORD="",
        SMTP_FROM="noreply@cloudless.gr",
        SMTP_USE_TLS=False,
        SMTP_SSL_VERIFY=True,
        DIGEST_EMAIL_TO="ops@cloudless.gr",
        CLOUDFLARE_EMAIL_API_TOKEN="cf-tok",
        CLOUDFLARE_API_TOKEN="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_send_email_smtp_reply_to_and_extra_headers(monkeypatch):
    from app.services import email_digest

    captured = {}

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            pass

        def has_extn(self, name):
            return False

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(email_digest, "get_settings", lambda: _smtp_settings())
    monkeypatch.setattr(email_digest.smtplib, "SMTP", _SMTP)
    email_digest.send_email_smtp(
        subject="s",
        text_body="t",
        to_addrs=["a@b.c"],
        reply_to="reply@x.y",
        extra_headers={"X-Test": "1"},
    )
    assert captured["msg"]["Reply-To"] == "reply@x.y"
    assert captured["msg"]["X-Test"] == "1"


def test_send_email_cloudflare_smtp_reply_to_and_headers(monkeypatch):
    from app.services import email_digest

    captured = {}

    class _SMTPS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(email_digest, "get_settings", lambda: _smtp_settings())
    monkeypatch.setattr(email_digest.smtplib, "SMTP_SSL", _SMTPS)
    email_digest.send_email_cloudflare_smtp(
        subject="s",
        text_body="t",
        to_addrs=["a@b.c"],
        reply_to="reply@x.y",
        extra_headers={"X-CF": "1"},
    )
    assert captured["msg"]["Reply-To"] == "reply@x.y"
    assert captured["msg"]["X-CF"] == "1"


# ── whatsapp_cloud_client edges ──────────────────────────────────────────────


def test_extract_meta_error_non_dict_body():
    import httpx

    from app.services.whatsapp_cloud_client import _extract_meta_error

    resp = httpx.Response(400, content=b"[1,2]")
    assert _extract_meta_error(resp) is None


def test_human_error_message_fallback():
    from app.services.whatsapp_cloud_client import _human_error_message

    assert _human_error_message(None, "fallback msg") == "fallback msg"


# ── telegram_group_watch mention match ───────────────────────────────────────


def test_bot_mentioned_matches_entity_fragment():
    from app.services.telegram_group_watch import bot_mentioned

    entities = [{"type": "mention", "offset": 3, "length": 9}]
    assert bot_mentioned("hi @mybot_ok", entities, "mybot_ok") is True


# ── linkedin_ads_report parse + geo fallback ────────────────────────────────


def test_eur_int_valueerror_fallbacks():
    from app.services import linkedin_ads_report as R

    assert R._eur("x=1.2.3", r"x=([\d.]+)") == 0.0
    assert R._int("x=1.5", r"x=([\d.]+)") == 0


@pytest.mark.asyncio
async def test_geo_names_swallows_lookup_error(monkeypatch):
    from app.services import linkedin_ads_report as R

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, *a, **k):
            raise RuntimeError("api down")

    acct = SimpleNamespace(access_token_enc=b"enc")
    db = _DB(results=[_Res(first=acct)])
    monkeypatch.setattr(R, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda *a, **k: _Client())
    assert await R._geo_names(db, ["123"]) == {}


# ── misc one-liners ──────────────────────────────────────────────────────────


def test_score_engagement_empty_words():
    from app.services.content_scorer import _score_engagement

    assert _score_engagement("   ") == 0.0


def test_article_markdown_to_html_skips_empty_blocks():
    from app.services.blog_articles import article_markdown_to_html

    html = article_markdown_to_html("# Title\n\n\n\n- a\n- b")
    assert "Title" in html


def test_downscale_rgba_png_roundtrip():
    from PIL import Image

    from app.services.media_storage import downscale_image_bytes

    buf = io.BytesIO()
    Image.new("RGBA", (16, 16), (255, 0, 0, 128)).save(buf, "PNG")
    out, w, h = downscale_image_bytes(buf.getvalue(), max_edge=8)
    assert isinstance(out, bytes) and w == 8


def test_infographic_blends_background_texture():
    from PIL import Image

    from app.services.infographic_renderer import render_infographic

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    out = render_infographic({"title": "T", "subtitle": "s", "stats": []}, buf.getvalue(), 64, 64)
    assert isinstance(out, bytes) and len(out) > 100


@pytest.mark.asyncio
async def test_browser_session_queue_remove_error_swallowed(monkeypatch):
    from app.services import browser_orchestrator as BO

    class _R:
        async def rpush(self, *a):
            return 1

        async def lindex(self, *a):
            raise RuntimeError("redis died mid-wait")

    monkeypatch.setattr(BO, "_get_redis", AsyncMock(return_value=_R()))
    sess = BO.BrowserSession("instagram", bridge=SimpleNamespace())
    sess._remove_from_queue = AsyncMock(side_effect=RuntimeError("gone"))
    bridge = await sess.__aenter__()
    assert bridge is sess.bridge


@pytest.mark.asyncio
async def test_gpu_arbiter_post_job_free_failure_is_swallowed(monkeypatch):
    import contextlib

    import httpx as _httpx

    from app.services import dmr, gpu_arbiter

    class _R:
        async def set(self, *a, **k):
            return True

        async def get(self, *a, **k):
            return None

        async def delete(self, *a, **k):
            return 1

        async def eval(self, *a, **k):
            return 1

        async def aclose(self):
            pass

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, *a, **k):
            return SimpleNamespace(status_code=200, json=lambda: {"queue_running": []})

        async def post(self, *a, **k):
            raise RuntimeError("comfy down")

    @contextlib.asynccontextmanager
    async def _noop_slot(*a, **k):
        yield

    monkeypatch.setattr(gpu_arbiter, "_redis", AsyncMock(return_value=_R()))
    monkeypatch.setattr(gpu_arbiter, "dmr_slot", _noop_slot)
    monkeypatch.setattr(dmr, "_unload_idle_models", AsyncMock())
    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **k: _Client())
    async with gpu_arbiter.media_gpu_lock(acquire_timeout_s=1.0):
        pass


@pytest.mark.asyncio
async def test_dmr_raises_connection_error_after_retry(monkeypatch):
    import asyncio
    import contextlib

    import httpx

    from app.services import dmr, gpu_arbiter

    monkeypatch.setattr(gpu_arbiter, "await_media_idle", AsyncMock())
    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(dmr, "_get_semaphore", lambda: asyncio.Semaphore(1))

    @contextlib.asynccontextmanager
    async def _noop_slot(*a, **k):
        yield

    monkeypatch.setattr(gpu_arbiter, "dmr_slot", _noop_slot)

    client = SimpleNamespace(post=AsyncMock(side_effect=httpx.TimeoutException("slow")))
    monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(dmr.asyncio, "sleep", AsyncMock())

    with pytest.raises(ConnectionError, match="after retry"):
        await dmr._call_dmr_chat_internal("hi", _skip_health_check=True)


@pytest.mark.asyncio
async def test_messenger_sidecar_no_admin_token_returns_none(monkeypatch):
    from app.services import messenger_sidecar

    monkeypatch.setattr(messenger_sidecar, "_get_admin_token", AsyncMock(return_value=None))
    assert await messenger_sidecar._get_account_and_config("p1") is None


@pytest.mark.asyncio
async def test_linkedin_sidecar_http_error_raises(monkeypatch):
    import httpx

    from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, *a, **k):
            return httpx.Response(500, text="down")

    c = LinkedInSidecarClient("http://localhost:1")
    monkeypatch.setattr(LinkedInSidecarClient, "_client", property(lambda self: _C()))
    with pytest.raises(LinkedInSidecarError):
        await c.refresh_session()


# ── main.py static-mount line ────────────────────────────────────────────────


def test_main_mounts_uploads_when_dir_exists(monkeypatch, tmp_path):
    import importlib

    import app.main as M

    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    try:
        importlib.reload(M)
    finally:
        monkeypatch.undo()
        importlib.reload(M)


# ── brand API endpoints ──────────────────────────────────────────────────────


def _brand_obj(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=TEAM_ID,
        voice=None,
        visual=None,
        guidelines=None,
        assets=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_brand_get_returns_brand(monkeypatch):
    from app.api import brand as B

    brand = _brand_obj()
    db = _DB(results=[_Res(first=brand)], team=_team())
    out = await B.get_brand(TEAM_ID, current_user=None, db=db)
    assert out is brand


@pytest.mark.asyncio
async def test_brand_get_returns_none_on_404():
    from app.api import brand as B

    db = _DB(results=[_Res(first=None)], team=_team())
    out = await B.get_brand(TEAM_ID, current_user=None, db=db)
    assert out is None


@pytest.mark.asyncio
async def test_brand_voice_visual_guidelines_getters():
    from app.api import brand as B

    brand = _brand_obj(voice={"tone": "x"}, visual={"primary_color": "#fff"}, guidelines={"g": 1})
    db = _DB(results=[_Res(first=brand)], team=_team())
    assert await B.get_brand_voice(TEAM_ID, current_user=None, db=db) == {"tone": "x"}

    db = _DB(results=[_Res(first=brand)], team=_team())
    assert await B.get_brand_visual(TEAM_ID, current_user=None, db=db) == {"primary_color": "#fff"}

    db = _DB(results=[_Res(first=brand)], team=_team())
    assert await B.get_brand_guidelines(TEAM_ID, current_user=None, db=db) == {"g": 1}


@pytest.mark.asyncio
async def test_update_brand_visual_patches_existing(monkeypatch):
    from app.api import brand as B

    visual = SimpleNamespace(primary_color="#000")
    brand = _brand_obj(visual=visual)
    db = _DB(results=[_Res(first=brand)], team=_team())
    data = MagicMock()
    data.model_dump = lambda exclude_unset=True: {"primary_color": "#fff"}
    await B.update_brand_visual(data, TEAM_ID, current_user=None, db=db)
    assert visual.primary_color == "#fff"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_analyze_voice_500_on_error(monkeypatch):
    from app.api import brand as B
    from app.services.brand_voice import analyze_brand_voice as _abv  # noqa: F401

    monkeypatch.setattr(
        "app.services.brand_voice.analyze_brand_voice",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )
    data = MagicMock()
    data.samples = ["s1"]
    with pytest.raises(HTTPException) as ei:
        await B.analyze_voice(data, current_user=None)
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_list_brand_mentions_with_platform_filter():
    from app.api import brand as B

    brand = _brand_obj()
    mention = SimpleNamespace(id=1, platform="twitter")
    db = _DB(results=[_Res(first=brand), _Res(all=[mention])], team=_team())
    out = await B.list_brand_mentions(TEAM_ID, platform="twitter", limit=50, current_user=None, db=db)
    assert list(out) == [mention]
