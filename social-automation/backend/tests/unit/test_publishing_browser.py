"""Unit tests for the Instagram browser-CDP publish lane (in-page fetch)."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import publishing as pub


@pytest.fixture
def ig_account():
    return SimpleNamespace(
        account_id="17841463022505300",
        username="cloudless.gr",
        platform="instagram",
        meta_data={"private_api_ds_user_id": "36910035385"},
    )


@pytest.fixture
def ig_post():
    return SimpleNamespace(platform_specific={})


@pytest.fixture(autouse=True)
def _media_url_probe_ok(monkeypatch):
    monkeypatch.setattr(
        pub,
        "_probe_public_media",
        AsyncMock(return_value=[{"url": "u", "ok": True}]),
    )


@pytest.fixture(autouse=True)
def _browser_cdp_on(monkeypatch):
    monkeypatch.setattr(pub._settings, "INSTAGRAM_BROWSER_CDP_URL", "http://cdp.test:9229")


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    """Collapse the poll loop's 4s sleeps; monotonic auto-advances so a
    non-terminating poll exits at the code's deadline after a few polls."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    clock = {"t": 1_000.0}

    def _mono():
        clock["t"] += 30.0
        return clock["t"]

    monkeypatch.setattr(time, "monotonic", _mono)
    return clock


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHTTPClient:
    """Async-context httpx client stub returning a fixed /json payload."""

    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self._exc is not None:
            raise self._exc
        return _FakeHTTPResponse(self._payload)


IG_TARGETS = [
    {"type": "page", "url": "https://www.instagram.com/cloudless.gr/", "webSocketDebuggerUrl": "ws://cdp/ig"},
    {"type": "page", "url": "https://www.facebook.com/", "webSocketDebuggerUrl": "ws://cdp/fb"},
]


def _http(payload=None, exc=None):
    return lambda *a, **kw: _FakeHTTPClient(payload, exc)


def _cdp_dispatcher(states, *, uid="36910035385", calls=None):
    """_cdp_eval stub: cookie probe → uid; poll 'window.__igpub' → next state."""

    async def _eval(ws_url, expression, **kwargs):
        if calls is not None:
            calls.append(expression)
        if "ds_user_id" in expression:
            return uid
        if expression.strip() == "window.__igpub":
            return states.pop(0) if states else "pending"
        return "ok"

    return AsyncMock(side_effect=_eval)


def _jpeg(path):
    # Minimal JPEG bytes — PIL verifies the header even for tiny fixtures
    # only when asked to decode; open() only needs the SOI marker for size
    # probing? No — Image.open on truncated JPEG raises. Use a real 1x1 JPEG.
    import io

    from PIL import Image

    img = Image.new("RGB", (4, 3), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    path.write_bytes(buf.getvalue())
    return str(path)


# ── _cdp_eval ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cdp_eval_returns_value():
    ws = AsyncMock()
    ws.recv = AsyncMock(
        side_effect=[
            json.dumps({"id": 9, "method": "x"}),
            json.dumps({"id": 1, "result": {"result": {"value": "hello"}}}),
        ]
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("websockets.connect", return_value=cm):
        assert await pub._cdp_eval("ws://x", "1+1") == "hello"
    ws.send.assert_awaited_once()


# ── _publish_instagram_via_browser — early exits ───────────────────────


@pytest.mark.asyncio
async def test_browser_lane_disabled_without_url(ig_account, ig_post, monkeypatch):
    monkeypatch.setattr(pub._settings, "INSTAGRAM_BROWSER_CDP_URL", "")
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, ["/tmp/x.jpg"])
    assert result.success is False
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_browser_lane_no_media_skips(ig_account, ig_post):
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [])
    assert result.success is False and result.skipped is True


@pytest.mark.asyncio
async def test_browser_lane_mixed_video_set_rejected(ig_account, ig_post):
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, ["/a.mp4", "/b.jpg"])
    assert result.success is False
    assert "single video" in result.error


@pytest.mark.asyncio
async def test_browser_lane_video_delegates_to_reel(ig_account, ig_post, monkeypatch):
    sentinel = pub.PublishResult(success=True, platform_post_id="42")
    reel = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(pub, "_publish_instagram_reel_via_browser", reel)
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, ["/v.mp4"])
    assert result is sentinel
    assert reel.await_args.args[3] == "/v.mp4"


@pytest.mark.asyncio
async def test_browser_lane_cdp_unreachable(ig_account, ig_post, tmp_path, monkeypatch):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(exc=ConnectionError("down")))
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [_jpeg(tmp_path / "a.jpg")])
    assert result.success is False and "unreachable" in result.error


@pytest.mark.asyncio
async def test_browser_lane_no_ig_tab(ig_account, ig_post, tmp_path, monkeypatch):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http([{"type": "page", "url": "https://x.com/"}]))
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [_jpeg(tmp_path / "a.jpg")])
    assert result.success is False and "No live instagram.com tab" in result.error


@pytest.mark.asyncio
async def test_browser_lane_login_tab_ignored(ig_account, ig_post, tmp_path, monkeypatch):
    monkeypatch.setattr(
        pub.httpx,
        "AsyncClient",
        _http([{"type": "page", "url": "https://www.instagram.com/accounts/login/", "webSocketDebuggerUrl": "ws://x"}]),
    )
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [_jpeg(tmp_path / "a.jpg")])
    assert "No live instagram.com tab" in result.error


@pytest.mark.asyncio
async def test_browser_lane_cookie_probe_fails(ig_account, ig_post, tmp_path, monkeypatch):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    monkeypatch.setattr(pub, "_cdp_eval", AsyncMock(side_effect=RuntimeError("ws dead")))
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [_jpeg(tmp_path / "a.jpg")])
    assert "CDP session probe failed" in result.error


@pytest.mark.asyncio
async def test_browser_lane_uid_mismatch(ig_account, ig_post, tmp_path, monkeypatch):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    monkeypatch.setattr(pub, "_cdp_eval", _cdp_dispatcher([], uid="99999999999"))
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [_jpeg(tmp_path / "a.jpg")])
    assert "expected 36910035385" in result.error


@pytest.mark.asyncio
async def test_browser_lane_bad_image(ig_account, ig_post, tmp_path, monkeypatch):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    monkeypatch.setattr(pub, "_cdp_eval", _cdp_dispatcher([]))
    result = await pub._publish_instagram_via_browser(ig_account, "cap", ig_post, [str(bad)])
    assert "Failed to read image" in result.error


# ── image publish flow — poll-state branches ───────────────────────────


async def _run_image_lane(monkeypatch, tmp_path, states, images=1):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    calls = []
    monkeypatch.setattr(pub, "_cdp_eval", _cdp_dispatcher(states, calls=calls))
    paths = [_jpeg(tmp_path / f"i{k}.jpg") for k in range(images)]
    acct = SimpleNamespace(
        account_id="1", username="cloudless.gr", platform="instagram",
        meta_data={"private_api_ds_user_id": "36910035385"},
    )
    post = SimpleNamespace(platform_specific={})
    return await pub._publish_instagram_via_browser(acct, "hello", post, paths), calls


@pytest.mark.asyncio
async def test_browser_lane_missing_cookies(tmp_path, monkeypatch):
    result, _ = await _run_image_lane(monkeypatch, tmp_path, ["missing-cookies"])
    assert result.success is False and "session expired" in result.error


@pytest.mark.asyncio
async def test_browser_lane_r1_error(tmp_path, monkeypatch):
    result, _ = await _run_image_lane(monkeypatch, tmp_path, ["r1:0:403:login_required"])
    assert result.success is False and "403" in result.error


@pytest.mark.asyncio
async def test_browser_lane_pending_timeout_is_ambiguous(tmp_path, monkeypatch):
    result, _ = await _run_image_lane(monkeypatch, tmp_path, ["pending"])
    assert result.success is False and result.ambiguous is True


@pytest.mark.asyncio
async def test_browser_lane_exc_state_errors(tmp_path, monkeypatch):
    result, _ = await _run_image_lane(monkeypatch, tmp_path, ["EXC ReferenceError: fetch"])
    assert result.success is False and "EXC" in result.error


@pytest.mark.asyncio
async def test_browser_lane_r2_empty_body_ambiguous(tmp_path, monkeypatch):
    result, _ = await _run_image_lane(monkeypatch, tmp_path, ["r2:200:"])
    assert result.success is False and result.ambiguous is True
    assert "no media id" in result.error


@pytest.mark.asyncio
async def test_browser_lane_success_single(tmp_path, monkeypatch):
    body = json.dumps({"media": {"id": "777", "code": "DgTEST"}})
    result, calls = await _run_image_lane(monkeypatch, tmp_path, [f"r2:200:{body}"])
    assert result.success is True
    assert result.platform_post_id == "777"
    assert result.platform_url == "https://www.instagram.com/p/DgTEST/"
    # carousel configure must NOT appear for a single image
    assert not any("configure_sidecar" in c for c in calls)


@pytest.mark.asyncio
async def test_browser_lane_success_carousel_uses_sidecar(tmp_path, monkeypatch):
    body = json.dumps({"media": {"id": "888", "code": "DgCAR"}})
    result, calls = await _run_image_lane(monkeypatch, tmp_path, [f"r2:200:{body}"], images=3)
    assert result.success is True and result.platform_post_id == "888"
    assert any("configure_sidecar" in c for c in calls)


@pytest.mark.asyncio
async def test_browser_lane_poll_survives_eval_drop(tmp_path, monkeypatch):
    # First poll raises → 'continue'; second returns the r2 result.
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    seen = {"n": 0}

    async def _eval(ws_url, expression, **kw):
        if "ds_user_id" in expression:
            return "36910035385"
        if expression.strip() == "window.__igpub":
            seen["n"] += 1
            if seen["n"] == 1:
                raise ConnectionError("ws lost")
            return 'r2:200:{"media":{"id":"9","code":"Dg9"}}'
        return "ok"

    monkeypatch.setattr(pub, "_cdp_eval", AsyncMock(side_effect=_eval))
    path = _jpeg(tmp_path / "x.jpg")
    acct = SimpleNamespace(account_id="1", username="u", platform="instagram", meta_data={"private_api_ds_user_id": "36910035385"})
    result = await pub._publish_instagram_via_browser(acct, "t", SimpleNamespace(platform_specific={}), [path])
    assert result.success is True and result.platform_post_id == "9"


# ── media probe helpers ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_video_no_ffprobe(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: None)
    assert await pub._ig_probe_video("/v.mp4") is None


@pytest.mark.asyncio
async def test_probe_video_ok(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/ffprobe")
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {
                    "format": {"duration": "26.5"},
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
                }
            ).encode(),
            b"",
        )
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await pub._ig_probe_video("/v.mp4") == (26.5, 1080, 1920)


@pytest.mark.asyncio
async def test_probe_video_bad_output(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/ffprobe")
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"not-json", b""))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await pub._ig_probe_video("/v.mp4") is None


@pytest.mark.asyncio
async def test_thumbnail_no_ffmpeg(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: None)
    assert await pub._ig_video_thumbnail_b64("/v.mp4", 10.0) is None


@pytest.mark.asyncio
async def test_thumbnail_success(monkeypatch, tmp_path):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/ffmpeg")
    payload = b"\xff\xd8\xff\xe0fake-frame"

    async def _ffmpeg(*args, **kw):
        path = args[-1]
        with open(path, "wb") as f:
            f.write(payload)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ffmpeg)
    out = await pub._ig_video_thumbnail_b64("/v.mp4", 10.0)
    assert base64.b64decode(out) == payload


@pytest.mark.asyncio
async def test_thumbnail_ffmpeg_fails(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/ffmpeg")
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"err"))
    proc.returncode = 1
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await pub._ig_video_thumbnail_b64("/v.mp4", 10.0) is None


# ── reel lane ──────────────────────────────────────────────────────────


@pytest.fixture
def _reel_mocks(monkeypatch):
    monkeypatch.setattr(pub, "_ig_probe_video", AsyncMock(return_value=(26.0, 1080, 1920)))
    monkeypatch.setattr(pub, "_ig_video_thumbnail_b64", AsyncMock(return_value="dGh1bWI="))


async def _run_reel_lane(monkeypatch, tmp_path, states, video_bytes=b"fake-video"):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    monkeypatch.setattr(pub, "_cdp_eval", _cdp_dispatcher(states))
    vp = tmp_path / "v.mp4"
    vp.write_bytes(video_bytes)
    acct = SimpleNamespace(
        account_id="1", username="cloudless.gr", platform="instagram",
        meta_data={"private_api_ds_user_id": "36910035385"},
    )
    return await pub._publish_instagram_reel_via_browser(
        acct, "reel caption", SimpleNamespace(platform_specific={}), str(vp),
        "http://cdp.test:9229", "36910035385",
    )


@pytest.mark.asyncio
async def test_reel_probe_fails(monkeypatch, ig_account, _reel_mocks):
    monkeypatch.setattr(pub, "_ig_probe_video", AsyncMock(return_value=None))
    result = await pub._publish_instagram_reel_via_browser(
        ig_account, "t", SimpleNamespace(platform_specific={}), "/v.mp4", "http://x", "1"
    )
    assert result.success is False and "ffprobe" in result.error


@pytest.mark.asyncio
async def test_reel_thumb_fails(monkeypatch, ig_account, _reel_mocks):
    monkeypatch.setattr(pub, "_ig_video_thumbnail_b64", AsyncMock(return_value=None))
    result = await pub._publish_instagram_reel_via_browser(
        ig_account, "t", SimpleNamespace(platform_specific={}), "/v.mp4", "http://x", "1"
    )
    assert result.success is False and "thumbnail" in result.error


@pytest.mark.asyncio
async def test_reel_unreadable_video(monkeypatch, ig_account, _reel_mocks):
    result = await pub._publish_instagram_reel_via_browser(
        ig_account, "t", SimpleNamespace(platform_specific={}), "/nonexistent/v.mp4", "http://x", "1"
    )
    assert result.success is False and "Failed to read video" in result.error


@pytest.mark.asyncio
async def test_reel_cdp_unreachable(monkeypatch, tmp_path, _reel_mocks):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(exc=ConnectionError("down")))
    vp = tmp_path / "v.mp4"
    vp.write_bytes(b"v")
    acct = SimpleNamespace(account_id="1", username="u", platform="instagram", meta_data={})
    result = await pub._publish_instagram_reel_via_browser(
        acct, "t", SimpleNamespace(platform_specific={}), str(vp), "http://x", None
    )
    assert "unreachable" in result.error


@pytest.mark.asyncio
async def test_reel_no_tab(monkeypatch, tmp_path, _reel_mocks):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http([]))
    vp = tmp_path / "v.mp4"
    vp.write_bytes(b"v")
    result = await pub._publish_instagram_reel_via_browser(
        SimpleNamespace(account_id="1", username="u", platform="instagram", meta_data={}),
        "t", SimpleNamespace(platform_specific={}), str(vp), "http://x", None,
    )
    assert "No live instagram.com tab" in result.error


@pytest.mark.asyncio
async def test_reel_uid_mismatch(monkeypatch, tmp_path, _reel_mocks):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))
    monkeypatch.setattr(pub, "_cdp_eval", _cdp_dispatcher([], uid="111"))
    vp = tmp_path / "v.mp4"
    vp.write_bytes(b"v")
    result = await pub._publish_instagram_reel_via_browser(
        SimpleNamespace(account_id="1", username="u", platform="instagram", meta_data={}),
        "t", SimpleNamespace(platform_specific={}), str(vp), "http://x", "36910035385",
    )
    assert "expected 36910035385" in result.error


@pytest.mark.asyncio
async def test_reel_vinit_rejected_not_ambiguous(monkeypatch, tmp_path, _reel_mocks):
    # Pre-configure failure (session revoked) → plain error, no ambiguity
    result = await _run_reel_lane(monkeypatch, tmp_path, ["vinit:403:login_required"])
    assert result.success is False and result.ambiguous is False
    assert "vinit:403" in result.error


@pytest.mark.asyncio
async def test_reel_missing_cookies(monkeypatch, tmp_path, _reel_mocks):
    result = await _run_reel_lane(monkeypatch, tmp_path, ["missing-cookies"])
    assert result.success is False and "session expired" in result.error


@pytest.mark.asyncio
async def test_reel_uploaded_then_lost_is_ambiguous(monkeypatch, tmp_path, _reel_mocks):
    monkeypatch.setattr(pub.httpx, "AsyncClient", _http(IG_TARGETS))

    async def _eval(ws_url, expression, **kw):
        if "ds_user_id" in expression:
            return "36910035385"
        if expression.strip() == "window.__igpub":
            return "uploaded"
        return "ok"

    monkeypatch.setattr(pub, "_cdp_eval", AsyncMock(side_effect=_eval))
    vp = tmp_path / "v.mp4"
    vp.write_bytes(b"v")
    result = await pub._publish_instagram_reel_via_browser(
        SimpleNamespace(account_id="1", username="u", platform="instagram", meta_data={}),
        "t", SimpleNamespace(platform_specific={}), str(vp), "http://x", None,
    )
    assert result.success is False and result.ambiguous is True


@pytest.mark.asyncio
async def test_reel_configure_empty_body_ambiguous(monkeypatch, tmp_path, _reel_mocks):
    result = await _run_reel_lane(monkeypatch, tmp_path, ["r2:200:EMPTY"])
    assert result.success is False and result.ambiguous is True


@pytest.mark.asyncio
async def test_reel_configure_non200_errors(monkeypatch, tmp_path, _reel_mocks):
    result = await _run_reel_lane(monkeypatch, tmp_path, ["r2:400:BADJSON:x"])
    assert result.success is False and "configure failed" in result.error


@pytest.mark.asyncio
async def test_reel_timeout_state_ambiguous(monkeypatch, tmp_path, _reel_mocks):
    result = await _run_reel_lane(monkeypatch, tmp_path, ["r2:timeout:transcode pending"])
    assert result.success is False and result.ambiguous is True


@pytest.mark.asyncio
async def test_reel_success_with_media(monkeypatch, tmp_path, _reel_mocks):
    body = json.dumps({"media": {"id": "314", "code": "DgREEL"}})
    result = await _run_reel_lane(monkeypatch, tmp_path, [f"r2:200:{body}"])
    assert result.success is True
    assert result.platform_post_id == "314"
    assert result.platform_url == "https://www.instagram.com/p/DgREEL/"


@pytest.mark.asyncio
async def test_reel_success_without_code(monkeypatch, tmp_path, _reel_mocks):
    result = await _run_reel_lane(monkeypatch, tmp_path, ['r2:200:{"media":{"id":"315"}}'])
    assert result.success is True and result.platform_url is None


# ── chain wiring: browser lane inside _publish_instagram ───────────────


@pytest.mark.asyncio
async def test_chain_returns_browser_result(monkeypatch, ig_account, ig_post, tmp_path):
    img = _jpeg(tmp_path / "c.jpg")
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_browser",
        AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="55", platform_url="https://x/55")),
    )
    result = await pub._publish_instagram("tok", "cap", ig_account, ig_post, [img], ["sp/img.jpg"])
    assert result.success is True
    assert result.platform_meta.get("publish_path") == "browser_cdp"


@pytest.mark.asyncio
async def test_chain_ambiguous_browser_stops_ladder(monkeypatch, ig_account, ig_post, tmp_path):
    img = _jpeg(tmp_path / "c.jpg")
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_browser",
        AsyncMock(return_value=pub.PublishResult(success=False, ambiguous=True, error="lost mid-flight")),
    )
    web = AsyncMock()
    monkeypatch.setattr(pub, "_publish_instagram_via_web", web)
    result = await pub._publish_instagram("tok", "cap", ig_account, ig_post, [img], ["sp/img.jpg"])
    assert result.ambiguous is True
    web.assert_not_awaited()  # must not fall through to the session-transplant lane


@pytest.mark.asyncio
async def test_chain_browser_fail_falls_through(monkeypatch, ig_account, ig_post, tmp_path):
    img = _jpeg(tmp_path / "c.jpg")
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_browser",
        AsyncMock(return_value=pub.PublishResult(success=False, error="No live instagram.com tab")),
    )
    monkeypatch.setattr(
        pub,
        "_publish_instagram_via_web",
        AsyncMock(return_value=pub.PublishResult(success=True, platform_post_id="66")),
    )
    ig_account.meta_data.update(
        {
            "private_api_session_id": "sid",
            "private_api_csrf_token": "csrf",
            "private_api_ds_user_id": "36910035385",
        }
    )
    result = await pub._publish_instagram("tok", "cap", ig_account, ig_post, [img], ["sp/img.jpg"])
    assert result.success is True and result.platform_post_id == "66"
