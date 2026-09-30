"""Unit tests for the free X web fallback (tweety/twscrape) — never hits X."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.services import publishing as pub
from app.services import x_web

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


def _settings(**over):
    base = dict(
        X_WEB_FALLBACK_ENABLED=True,
        X_WEB_AUTH_TOKEN="tok",
        X_WEB_CT0="csrf",
        X_WEB_COOKIES_JSON="",
        X_WEB_PROXY="",
        X_WEB_MAX_POSTS_PER_DAY=5,
        X_WEB_MIN_GAP_MINUTES=10,
        X_WEB_MAX_GAP_MINUTES=30,
        X_WEB_BREAKER_HOURS=6.0,
        X_WEB_ANALYTICS_MIN_INTERVAL_HOURS=6.0,
        X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS=10,
        X_WEB_STATE_FILE="/tmp/x_web_state_test.json",
        REDIS_URL="redis://invalid:1/0",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def settings(monkeypatch):
    s = _settings()
    monkeypatch.setattr(x_web, "get_settings", lambda: s)
    return s


class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _guard(clock=None, alert=None):
    return x_web.XWebGuard(x_web.MemoryGuardStore(), now=clock or _Clock(), alert=alert)


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ── media validation ─────────────────────────────────────────────────────────


def test_plan_media_text_only():
    plan = x_web.plan_media([])
    assert plan.kind == "text" and not plan.has_media


def test_plan_media_single_and_multi_image(tmp_path):
    paths = [_write(tmp_path, f"a{i}.jpg", JPEG) for i in range(4)]
    assert x_web.plan_media(paths[:1]).kind == "images"
    assert x_web.plan_media(paths).paths == paths


def test_plan_media_rejects_more_than_four_images(tmp_path):
    paths = [_write(tmp_path, f"a{i}.jpg", JPEG) for i in range(5)]
    with pytest.raises(x_web.XWebMediaError, match="at most 4"):
        x_web.plan_media(paths)


def test_plan_media_video_and_gif(tmp_path):
    assert x_web.plan_media([_write(tmp_path, "v.MP4", MP4)]).kind == "video"
    assert x_web.plan_media([_write(tmp_path, "g.gif", GIF)]).kind == "gif"


def test_plan_media_rejects_mixed_video_and_images(tmp_path):
    with pytest.raises(x_web.XWebMediaError, match="mixes"):
        x_web.plan_media([_write(tmp_path, "v.mp4", MP4), _write(tmp_path, "a.jpg", JPEG)])


def test_plan_media_rejects_mismatched_content(tmp_path):
    with pytest.raises(x_web.XWebMediaError, match="does not match"):
        x_web.plan_media([_write(tmp_path, "fake.mp4", JPEG)])


def test_plan_media_rejects_missing_empty_and_unsupported(tmp_path):
    with pytest.raises(x_web.XWebMediaError, match="missing"):
        x_web.plan_media([str(tmp_path / "nope.jpg")])
    with pytest.raises(x_web.XWebMediaError, match="empty"):
        x_web.plan_media([_write(tmp_path, "e.jpg", b"")])
    with pytest.raises(x_web.XWebMediaError, match="unsupported"):
        x_web.plan_media([_write(tmp_path, "doc.pdf", b"%PDF-1.7")])


def test_prepare_media_converts_png_to_jpeg(tmp_path):
    src = tmp_path / "logo.PNG"
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(src, "PNG")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    plan = x_web.plan_media([str(src)])
    (out,) = x_web.prepare_media(plan, str(out_dir))
    assert out.endswith(".jpg")
    with Image.open(out) as im:
        assert im.format == "JPEG" and im.mode == "RGB"


# ── breaker + limits ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_cap_defers_after_limit(settings):
    settings.X_WEB_MAX_POSTS_PER_DAY = 2
    settings.X_WEB_MIN_GAP_MINUTES = 0
    settings.X_WEB_MAX_GAP_MINUTES = 0
    clock = _Clock()
    g = _guard(clock)
    assert (await g.reserve_post_slot()).ok
    clock.t += 60
    assert (await g.reserve_post_slot()).ok
    clock.t += 60
    d = await g.reserve_post_slot()
    assert not d.ok and not d.breaker_open
    assert "daily cap" in (d.reason or "")
    assert d.retry_after == datetime.fromtimestamp(1_800_000_000.0 + 24 * 3600, UTC)
    clock.t = 1_800_000_000.0 + 24 * 3600 + 1  # oldest post aged out
    assert (await g.reserve_post_slot()).ok


@pytest.mark.asyncio
async def test_min_gap_between_posts(settings):
    clock = _Clock()
    g = _guard(clock)
    assert (await g.reserve_post_slot()).ok
    clock.t += 5 * 60  # < 10 min minimum
    d = await g.reserve_post_slot()
    assert not d.ok and "minimum gap" in (d.reason or "")
    assert d.retry_after is not None
    clock.t += 30 * 60  # > 30 min maximum
    assert (await g.reserve_post_slot()).ok


@pytest.mark.asyncio
async def test_breaker_trip_blocks_and_alerts_once(settings):
    alert = AsyncMock()
    clock = _Clock()
    g = _guard(clock, alert)
    assert await g.trip("LockedAccount: Your Account is Locked") is True
    assert await g.trip("again") is False
    alert.assert_awaited_once()
    d = await g.reserve_post_slot()
    assert not d.ok and d.breaker_open and "circuit breaker" in (d.reason or "")
    assert not (await g.reserve_analytics_slot()).ok
    clock.t += 6 * 3600 + 1
    assert (await g.breaker_state())[0] is False
    assert (await g.reserve_post_slot()).ok


@pytest.mark.asyncio
async def test_analytics_interval(settings):
    clock = _Clock()
    g = _guard(clock)
    assert (await g.reserve_analytics_slot()).ok
    clock.t += 3600
    assert not (await g.reserve_analytics_slot()).ok
    clock.t += 5 * 3600 + 1
    assert (await g.reserve_analytics_slot()).ok


@pytest.mark.asyncio
async def test_file_store_roundtrip(tmp_path, settings):
    store = x_web.FileGuardStore(str(tmp_path / "state.json"))
    g = x_web.XWebGuard(store, now=_Clock())
    await g.trip("HTTP 403: forbidden")
    g2 = x_web.XWebGuard(x_web.FileGuardStore(str(tmp_path / "state.json")), now=_Clock())
    assert (await g2.breaker_state())[0] is True


class LockedAccount(Exception):
    pass


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


@pytest.mark.parametrize(
    "exc, trips",
    [
        (LockedAccount("Your Account is Locked"), True),
        (_HTTPError(401), True),
        (_HTTPError(403), True),
        (_HTTPError(429), True),
        (RuntimeError("Couldn't get x-client-transaction-id"), True),
        (RuntimeError("palm_error: blocked"), True),
        (RuntimeError("account suspended"), True),
        (RuntimeError("connection reset by peer"), False),
        (_HTTPError(500), False),
    ],
)
def test_classify_error(exc, trips):
    assert bool(x_web.classify_x_web_error(exc)) is trips


# ── publish via tweety (mocked) ──────────────────────────────────────────────


class _FakeTweety:
    def __init__(self, username="TBaltzakis", fail_on=None, media_ok=True):
        self.user = SimpleNamespace(username=username)
        self.created: list[dict] = []
        self.uploaded: list[list[str]] = []
        self._fail_on = fail_on
        self._media_ok = media_ok
        self._next = 1000

    async def upload_media(self, files):
        self.uploaded.append(list(files))
        return [SimpleNamespace(media_id=f"m{i}" if self._media_ok else None) for i, _ in enumerate(files)]

    async def create_tweet(self, text, files=None, reply_to=None):
        if self._fail_on is not None and len(self.created) == self._fail_on[0]:
            raise self._fail_on[1]
        self._next += 1
        self.created.append({"text": text, "files": files, "reply_to": reply_to})
        return SimpleNamespace(id=str(self._next))


def _factory(client):
    async def make():
        return client

    return make


@pytest.mark.asyncio
async def test_publish_via_x_web_images_and_thread(tmp_path, settings):
    fake = _FakeTweety()
    imgs = [_write(tmp_path, f"a{i}.jpg", JPEG) for i in range(2)]
    out = await x_web.publish_via_x_web(
        expected_username="@tbaltzakis", chunks=["one", "two", "three"], media_paths=imgs,
        guard=_guard(), client_factory=_factory(fake),
    )
    assert out.status == "ok" and out.tweet_ids == ["1001", "1002", "1003"]
    assert len(fake.uploaded[0]) == 2 and all(p.endswith(".jpg") for p in fake.uploaded[0])
    assert fake.created[0]["files"] and fake.created[1]["files"] is None
    assert fake.created[1]["reply_to"] == "1001" and fake.created[2]["reply_to"] == "1002"


@pytest.mark.asyncio
async def test_publish_via_x_web_video(tmp_path, settings):
    fake = _FakeTweety()
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["clip"], media_paths=[_write(tmp_path, "v.mp4", MP4)],
        guard=_guard(), client_factory=_factory(fake),
    )
    assert out.status == "ok"
    assert fake.uploaded[0][0].endswith(".mp4")


@pytest.mark.asyncio
async def test_publish_via_x_web_media_upload_failure_does_not_post(tmp_path, settings):
    fake = _FakeTweety(media_ok=False)
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["hi"], media_paths=[_write(tmp_path, "a.jpg", JPEG)],
        guard=_guard(), client_factory=_factory(fake),
    )
    assert out.status == "media_error" and "not posting without media" in (out.error or "")
    assert fake.created == []


@pytest.mark.asyncio
async def test_publish_via_x_web_invalid_media_does_not_login(tmp_path, settings):
    factory = AsyncMock()
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["hi"], media_paths=[_write(tmp_path, "d.pdf", b"%PDF")],
        guard=_guard(), client_factory=factory,
    )
    assert out.status == "media_error"
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_via_x_web_locked_trips_breaker(settings):
    alert = AsyncMock()
    g = _guard(alert=alert)
    fake = _FakeTweety(fail_on=(0, LockedAccount("Your Account is Locked")))
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["hi"], media_paths=[], guard=g, client_factory=_factory(fake),
    )
    assert out.status == "tripped" and "circuit breaker" in (out.error or "")
    assert (await g.breaker_state())[0] is True
    alert.assert_awaited_once()
    # Next attempt is refused without touching X.
    factory = AsyncMock()
    out2 = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["hi"], media_paths=[], guard=g, client_factory=factory,
    )
    assert out2.status == "breaker"
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_via_x_web_identity_mismatch(settings):
    fake = _FakeTweety(username="someoneelse")
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["hi"], media_paths=[], guard=_guard(), client_factory=_factory(fake),
    )
    assert out.status == "identity" and fake.created == []


@pytest.mark.asyncio
async def test_publish_via_x_web_partial_thread_reports_success(settings):
    fake = _FakeTweety(fail_on=(1, RuntimeError("network blip")))
    out = await x_web.publish_via_x_web(
        expected_username="tbaltzakis", chunks=["a", "b"], media_paths=[], guard=_guard(), client_factory=_factory(fake),
    )
    assert out.status == "ok" and out.partial and out.tweet_ids == ["1001"]


@pytest.mark.asyncio
async def test_publish_via_x_web_disabled(monkeypatch):
    monkeypatch.setattr(x_web, "get_settings", lambda: _settings(X_WEB_FALLBACK_ENABLED=False))
    out = await x_web.publish_via_x_web(expected_username="u", chunks=["hi"], media_paths=[])
    assert out.status == "unavailable"


def test_cookies_json_merged_with_explicit_vars(monkeypatch):
    monkeypatch.setattr(
        x_web, "get_settings",
        lambda: _settings(X_WEB_AUTH_TOKEN="", X_WEB_CT0="explicit", X_WEB_COOKIES_JSON='[{"name":"auth_token","value":"a"},{"name":"ct0","value":"old"}]'),
    )
    assert x_web.load_cookies() == {"auth_token": "a", "ct0": "explicit"}
    assert x_web.is_configured() is True


def test_not_configured_without_cookies(monkeypatch):
    monkeypatch.setattr(x_web, "get_settings", lambda: _settings(X_WEB_AUTH_TOKEN="", X_WEB_CT0=""))
    assert x_web.is_configured() is False


# ── publishing pipeline: fallback selection ──────────────────────────────────


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **kw):
        self.calls.append({"url": url, "json": json})
        return self.responses.pop(0) if self.responses else _Resp(500, "none")


@pytest.fixture
def account():
    return SimpleNamespace(account_id="42", username="TBaltzakis")


def _credits_depleted():
    return _Client([_Resp(402, {"title": "CreditsDepleted", "detail": "credits-depleted"})])


@pytest.mark.asyncio
async def test_402_falls_back_to_x_web_before_browser(account, settings, monkeypatch):
    web = AsyncMock(return_value=x_web.XWebOutcome(status="ok", tweet_ids=["777"]))
    browser = AsyncMock()
    monkeypatch.setattr(x_web, "publish_via_x_web", web)
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    fake = _credits_depleted()
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: fake):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.success and res.platform_post_id == "777"
    assert res.platform_url == "https://x.com/TBaltzakis/status/777"
    assert res.platform_meta["x_web"]["provider"] == "x_web_tweety"
    web.assert_awaited_once()
    browser.assert_not_awaited()
    assert len(fake.calls) == 1  # official API tried first


@pytest.mark.asyncio
async def test_402_with_x_web_disabled_uses_browser(account, monkeypatch):
    monkeypatch.setattr(x_web, "get_settings", lambda: _settings(X_WEB_FALLBACK_ENABLED=False))
    web = AsyncMock()
    browser = AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="9"))
    monkeypatch.setattr(x_web, "publish_via_x_web", web)
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: _credits_depleted()):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.success and res.platform_post_id == "9"
    web.assert_not_awaited()


@pytest.mark.asyncio
async def test_x_web_transient_error_falls_through_to_browser(account, settings, monkeypatch):
    monkeypatch.setattr(x_web, "publish_via_x_web", AsyncMock(return_value=x_web.XWebOutcome(status="error", error="X web post failed: timeout")))
    browser = AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="5"))
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: _credits_depleted()):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert res.success and res.platform_post_id == "5"
    browser.assert_awaited_once()


@pytest.mark.asyncio
async def test_x_web_deferred_sets_retry_after(account, settings, monkeypatch):
    when = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        x_web, "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status="deferred", error="X web daily cap reached", retry_after=when)),
    )
    browser = AsyncMock()
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: _credits_depleted()):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert not res.success and not res.skipped and res.retry_after == when
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_x_web_tripped_is_permanent_failure(account, settings, monkeypatch):
    monkeypatch.setattr(
        x_web, "publish_via_x_web",
        AsyncMock(return_value=x_web.XWebOutcome(status="tripped", error="LockedAccount — circuit breaker tripped for 6h")),
    )
    browser = AsyncMock()
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: _credits_depleted()):
        res = await pub._publish_twitter("tok", "Hello!", account, SimpleNamespace(), [])
    assert not res.success and res.permanent and "circuit breaker" in res.error
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_skips_official_and_uses_x_web(account, settings, monkeypatch, tmp_path):
    web = AsyncMock(return_value=x_web.XWebOutcome(status="ok", tweet_ids=["888"]))
    monkeypatch.setattr(x_web, "publish_via_x_web", web)
    official = _Client([])
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: official):
        res = await pub._publish_twitter("tok", "clip", account, SimpleNamespace(), [_write(tmp_path, "v.mp4", MP4)])
    assert res.success and res.platform_post_id == "888"
    assert official.calls == []
    assert web.await_args.kwargs["media_paths"][0].endswith("v.mp4")


@pytest.mark.asyncio
async def test_video_without_x_web_never_posts_without_video(account, monkeypatch, tmp_path):
    monkeypatch.setattr(x_web, "get_settings", lambda: _settings(X_WEB_FALLBACK_ENABLED=False))
    browser = AsyncMock()
    monkeypatch.setattr(pub, "_publish_twitter_via_browser", browser)
    res = await pub._publish_twitter("tok", "clip", account, SimpleNamespace(), [_write(tmp_path, "v.mp4", MP4)])
    assert not res.success and "video" in res.error.lower()
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_media_fails_permanently(account, settings, tmp_path):
    paths = [_write(tmp_path, f"a{i}.jpg", JPEG) for i in range(5)]
    res = await pub._publish_twitter("tok", "hi", account, SimpleNamespace(), paths)
    assert not res.success and res.permanent and "at most 4" in res.error


@pytest.mark.asyncio
async def test_official_media_upload_failure_does_not_post_text_only(account, monkeypatch, tmp_path):
    monkeypatch.setattr(x_web, "get_settings", lambda: _settings(X_WEB_FALLBACK_ENABLED=False))

    async def boom(path):
        raise pub.TwitterAPIError(400, "bad media", "upload")

    monkeypatch.setattr(pub, "_twitter_upload_media", boom)
    official = _Client([_Resp(200, {"data": {"id": "1"}})])
    with patch("app.services.twitter_api.httpx.AsyncClient", new=lambda timeout=30.0: official):
        res = await pub._publish_twitter("tok", "hi", account, SimpleNamespace(), [_write(tmp_path, "a.jpg", JPEG)])
    assert not res.success and "not posting without media" in res.error
    assert official.calls == []


@pytest.mark.asyncio
async def test_official_media_upload_402_falls_back_with_media(account, settings, monkeypatch, tmp_path):
    async def depleted(path):
        raise pub.TwitterAPIError(402, "credits-depleted", "upload")

    monkeypatch.setattr(pub, "_twitter_upload_media", depleted)
    web = AsyncMock(return_value=x_web.XWebOutcome(status="ok", tweet_ids=["999"]))
    monkeypatch.setattr(x_web, "publish_via_x_web", web)
    img = _write(tmp_path, "a.jpg", JPEG)
    res = await pub._publish_twitter("tok", "hi", account, SimpleNamespace(), [img])
    assert res.success and res.platform_post_id == "999"
    assert web.await_args.kwargs["media_paths"] == [img]


@pytest.mark.asyncio
async def test_browser_bridge_refuses_unattachable_media(account, tmp_path):
    res = await pub._publish_twitter_via_browser(account, "hi", SimpleNamespace(), [str(tmp_path / "a.heic")])
    assert not res.success and "not publishing without it" in res.error


# ── analytics reads (mocked tweety/twscrape) ─────────────────────────────────


class _FakeReader:
    def __init__(self, fail=None):
        self.fail = fail
        self.detail_calls: list[str] = []

    async def get_user_info(self, username):
        if self.fail:
            raise self.fail
        return SimpleNamespace(followers_count=321, friends_count=10, statuses_count=99, listed_count=2)

    async def get_tweets(self, uid, pages=1):
        return SimpleNamespace(tweets=[
            SimpleNamespace(id="111", views="1,234", likes=5, reply_counts=1, retweet_counts=2, quote_counts=1, bookmark_count=0, text="t", created_on=None),
        ])

    async def tweet_detail(self, tid):
        self.detail_calls.append(tid)
        return SimpleNamespace(id=tid, views=50, likes=1, reply_counts=0, retweet_counts=0, quote_counts=0, bookmark_count=0, text="", created_on=None)


class _FakeTwscrape:
    async def user_by_login(self, login):
        return SimpleNamespace(id=42, followersCount=300, friendsCount=1, statusesCount=2, listedCount=0)

    async def user_tweets(self, uid, limit=-1):
        yield SimpleNamespace(id=222, viewCount=10, likeCount=1, replyCount=0, retweetCount=0, quoteCount=0, bookmarkedCount=0, rawContent="x", date=None)

    async def tweet_details(self, twid):
        return None


@pytest.mark.asyncio
async def test_fetch_analytics_tweety_primary_and_interval(settings):
    reader = _FakeReader()
    g = _guard()
    web, reason = await x_web.fetch_x_web_analytics(
        username="TBaltzakis", user_id="42", wanted_tweet_ids=["111", "333"], guard=g, tweety_factory=_factory(reader),
    )
    assert reason is None and web is not None
    assert web.source == "x_web_tweety" and web.followers == 321
    assert web.tweets["111"].views == 1234
    assert reader.detail_calls == ["333"]
    again, reason2 = await x_web.fetch_x_web_analytics(username="TBaltzakis", user_id="42", guard=g, tweety_factory=_factory(reader))
    assert again is None and "min interval" in (reason2 or "")


@pytest.mark.asyncio
async def test_fetch_analytics_falls_back_to_twscrape(settings):
    async def tws(handle):
        return _FakeTwscrape()

    web, _ = await x_web.fetch_x_web_analytics(
        username="TBaltzakis", user_id="42", guard=_guard(),
        tweety_factory=_factory(_FakeReader(fail=RuntimeError("graphql shape changed"))), twscrape_factory=tws,
    )
    assert web is not None and web.source == "x_web_twscrape" and "222" in web.tweets
    assert web.followers == 300


@pytest.mark.asyncio
async def test_fetch_analytics_trip_stops_reads(settings):
    g = _guard()
    tws = AsyncMock()
    web, reason = await x_web.fetch_x_web_analytics(
        username="TBaltzakis", user_id="42", guard=g,
        tweety_factory=_factory(_FakeReader(fail=_HTTPError(401))), twscrape_factory=tws,
    )
    assert web is None and "tripped" in (reason or "")
    tws.assert_not_awaited()
    assert (await g.breaker_state())[0] is True


def test_module_disables_twscrape_telemetry():
    assert os.environ.get("TWS_TELEMETRY") == "0"
