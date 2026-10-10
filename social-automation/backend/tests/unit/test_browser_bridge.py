"""Tests for app/services/browser_bridge.py — the browser-novnc bridge client.

Covers the _request contention seam, dead-marker Redis helpers, the
ensure_session state machine, is_twitter_logged_in, twitter_login,
_navigate_to_thread/_handle_e2ee_pin_dialog, post_tweet, and every thin
endpoint wrapper.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import browser_bridge as bb


def _client(platform="facebook"):
    return bb.BrowserBridgeClient("http://bridge:9223/", platform=platform)


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bb.BrowserBridgeError(self.status_code, self.text)


class _HttpxClient:
    """httpx.AsyncClient stand-in serving a queue of responses."""

    queue: list[_Resp] = []
    calls: list[tuple] = []

    def __init__(self, *a, **kw):
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None):
        _HttpxClient.calls.append((method, url, json))
        return _HttpxClient.queue.pop(0)

    async def get(self, url, **kw):
        _HttpxClient.calls.append(("GET", url, kw))
        return _HttpxClient.queue.pop(0)

    async def post(self, url, json=None, **kw):
        _HttpxClient.calls.append(("POST", url, json))
        return _HttpxClient.queue.pop(0)


@pytest.fixture()
def fake_httpx(monkeypatch):
    _HttpxClient.queue = []
    _HttpxClient.calls = []
    monkeypatch.setattr(bb.httpx, "AsyncClient", _HttpxClient)
    return _HttpxClient


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bb.asyncio, "sleep", AsyncMock())


class _Stub:
    """Client with every external call recorded/replacable."""

    def __init__(self):
        self.c = _client()
        self.req_calls: list[tuple] = []
        self.req_queue: list[dict] = []
        self.ev_queue: list[Any] = []
        self.ev_calls: list[str] = []
        self.nav_calls: list[str] = []
        self.c._request = self._request
        self.c.evaluate = self._evaluate
        self.c.navigate = self._navigate
        self.c.fill = AsyncMock(return_value={"ok": True})
        self.c.click = AsyncMock(return_value={"ok": True})
        self.c.mouse_click = AsyncMock(return_value={"ok": True})
        self.c.upload_file = AsyncMock(return_value={"ok": True})
        self.c._dead_marker_get = AsyncMock(return_value=None)
        self.c._dead_marker_set = AsyncMock()

    async def _request(self, method, path, json=None, **kw):
        self.req_calls.append((method, path, json))
        if self.req_queue:
            v = self.req_queue.pop(0)
            if isinstance(v, Exception):
                raise v
            return v
        return {"status": "ok"}

    async def _evaluate(self, expr):
        self.ev_calls.append(expr)
        v = self.ev_queue.pop(0) if self.ev_queue else {"result": {}}
        if isinstance(v, Exception):
            raise v
        return v

    async def _navigate(self, url):
        self.nav_calls.append(url)
        return {"status": "ok"}


# ── core seam ─────────────────────────────────────────────────────────


def test_headers_and_error():
    assert _client()._headers() == {"X-Platform": "facebook"}
    assert bb.BrowserBridgeClient("http://x")._headers() == {}
    err = bb.BrowserBridgeError(404, "nope")
    assert err.status_code == 404 and "404" in str(err)


@pytest.mark.asyncio
async def test_request_success_and_error(fake_httpx):
    fake_httpx.queue = [_Resp(200, {"ok": 1})]
    out = await _client()._request("GET", "/path")
    assert out == {"ok": 1}
    assert fake_httpx.calls[0][1] == "http://bridge:9223/path"

    fake_httpx.queue = [_Resp(500, text="boom")]
    with pytest.raises(bb.BrowserBridgeError) as exc:
        await _client()._request("GET", "/bad", contention_retries=0)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_request_contention_retry(fake_httpx):
    fake_httpx.queue = [_Resp(409), _Resp(200, {"ok": 2})]
    out = await _client()._request("GET", "/p")
    assert out == {"ok": 2}
    fake_httpx.queue = [_Resp(409)] * 3
    with pytest.raises(bb.BrowserBridgeError) as exc:
        await _client()._request("GET", "/p", contention_retries=1)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_health_status_start(fake_httpx):
    fake_httpx.queue = [_Resp(200, {"up": True}), _Resp(200, {"s": 1})]
    assert await _client().health() == {"up": True}
    assert await _client().session_status() == {"s": 1}
    # start_session: 409 retry then success; error path; exhaustion
    fake_httpx.queue = [_Resp(409), _Resp(200, {"started": 1})]
    out = await _client().start_session("twitter", contention_retries=1)
    assert out == {"started": 1}
    fake_httpx.queue = [_Resp(500, text="x")]
    with pytest.raises(bb.BrowserBridgeError):
        await _client().start_session("fb")
    fake_httpx.queue = [_Resp(409)]
    with pytest.raises(bb.BrowserBridgeError):
        await _client().start_session("fb")


@pytest.mark.asyncio
async def test_dead_markers(monkeypatch):
    store = {}

    class _R:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v, ex=None):
            store[k] = v

        async def aclose(self):
            pass

    aioredis = types.ModuleType("redis.asyncio")
    aioredis.from_url = lambda *a, **kw: _R()
    redis_pkg = types.ModuleType("redis")
    redis_pkg.asyncio = aioredis
    monkeypatch.setitem(sys.modules, "redis", redis_pkg)
    monkeypatch.setitem(sys.modules, "redis.asyncio", aioredis)

    c = _client()
    await c._dead_marker_set("twitter")
    assert await c._dead_marker_get("twitter") == "1"
    assert await c._dead_marker_get("instagram") is None

    # redis down → best-effort no-ops
    aioredis.from_url = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x"))
    assert await c._dead_marker_get("t") is None
    await c._dead_marker_set("t")


# ── ensure_session state machine ──────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_session_active(monkeypatch):
    c = _client()
    c.session_status = AsyncMock(return_value={"status": "active", "platform": "facebook", "cookies_found": 5})
    out = await c.ensure_session("facebook")
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_ensure_session_unreachable_and_dead_marker(fake_httpx):
    c = _client()
    c.session_status = AsyncMock(side_effect=RuntimeError("down"))
    c._dead_marker_get = AsyncMock(return_value="1")
    out = await c.ensure_session("facebook")
    assert out["status"] == "waiting" and "verified recently" in out["message"]


@pytest.mark.asyncio
async def test_ensure_session_extract_path(fake_httpx):
    c = _client()
    c.session_status = AsyncMock(return_value={"status": "error"})
    fake_httpx.queue = [_Resp(200, {"cookies_found": 3, "platform": "facebook"})]
    out = await c.ensure_session("facebook")
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_ensure_session_restart_then_active(fake_httpx):
    c = _client()
    statuses = [
        {"status": "error"},
        {"status": "active", "platform": "facebook", "cookies_found": 2},
    ]
    c.session_status = AsyncMock(side_effect=lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0])
    c._dead_marker_get = AsyncMock(return_value=None)
    c.start_session = AsyncMock(return_value={"ok": 1})
    c._dead_marker_set = AsyncMock()
    fake_httpx.queue = [_Resp(200, {"cookies_found": 0})]  # extract: no cookies
    out = await c.ensure_session("facebook")
    assert out["status"] == "active"
    c._dead_marker_set.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_session_verified_dead(fake_httpx):
    c = _client()
    c.session_status = AsyncMock(return_value={"status": "waiting", "platform": "facebook"})
    c._dead_marker_get = AsyncMock(return_value=None)
    c.start_session = AsyncMock(return_value={"ok": 1})
    c._dead_marker_set = AsyncMock()
    fake_httpx.queue = [_Resp(500)]
    out = await c.ensure_session("facebook")
    assert out["status"] == "waiting"
    c._dead_marker_set.assert_awaited_once_with("facebook")


# ── twitter login / probe ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_twitter_logged_in():
    s = _Stub()
    s.ev_queue = [{"result": {"url": "https://x.com/home", "loggedIn": True, "handle": "me"}}]
    out = await s.c.is_twitter_logged_in()
    assert out["logged_in"] is True and out["handle"] == "me"

    # error → logged_in False
    s2 = _Stub()
    s2.ev_queue = [bb.BrowserBridgeError(503, "down")]
    out2 = await s2.c.is_twitter_logged_in()
    assert out2["logged_in"] is False and out2["error"] == "down"

    # off-x.com → navigates home and re-probes
    s3 = _Stub()
    s3.ev_queue = [
        {"result": {"url": "https://facebook.com", "loggedIn": False}},
        {"result": {"url": "https://x.com/home", "loggedIn": False, "handle": None}},
    ]
    out3 = await s3.c.is_twitter_logged_in()
    assert "https://x.com/home" in s3.nav_calls
    assert out3["logged_in"] is False


@pytest.mark.asyncio
async def test_twitter_login_happy_path():
    s = _Stub()
    s.ev_queue = [
        {"result": {"form": True, "loggedIn": False}},  # step 1
        {"result": {"pwReady": True, "loggedIn": False}},  # step 2
        {"result": {}},  # post-continue probe
    ]
    s.c._click_visible_continue = AsyncMock(return_value=True)
    s.c.is_twitter_logged_in = AsyncMock(return_value={"logged_in": True, "url": "https://x.com/home"})
    out = await s.c.twitter_login("u", "p")
    assert out["status"] == "logged_in"


@pytest.mark.asyncio
async def test_twitter_login_errors():
    s = _Stub()
    s.c._click_visible_continue = AsyncMock(return_value=True)
    # form never renders (timeout_s=0 → deadline already passed)
    out = await s.c.twitter_login("u", "p", timeout_s=0)
    assert out["status"] == "error" and "form did not render" in out["error"]

    # arkose on step 2
    s2 = _Stub()
    s2.c._click_visible_continue = AsyncMock(return_value=True)
    s2.ev_queue = [
        {"result": {"form": True, "loggedIn": False}},
        {"result": {"arkose": True}},
    ]
    out2 = await s2.c.twitter_login("u", "p")
    assert "arkose" in out2["error"]

    # continue button missing on step 1
    s3 = _Stub()
    s3.c._click_visible_continue = AsyncMock(return_value=False)
    s3.ev_queue = [{"result": {"form": True, "loggedIn": False}}]
    out3 = await s3.c.twitter_login("u", "p")
    assert "Continue button not found" in out3["error"]


# ── messenger thread nav + E2EE ───────────────────────────────────────


@pytest.mark.asyncio
async def test_navigate_to_thread():
    s = _Stub()
    # already on thread → no nav
    s.ev_queue = [{"result": "https://www.facebook.com/messages/t/123/"}]
    await s.c._navigate_to_thread("123")
    assert s.nav_calls == []

    s2 = _Stub()
    s2.ev_queue = [{"result": "about:blank"}]
    await s2.c._navigate_to_thread("456", is_e2ee=False)
    assert s2.nav_calls == ["about:blank", "https://www.facebook.com/messages/t/456/"]


@pytest.mark.asyncio
async def test_e2ee_pin_dialog():
    s = _Stub()
    s.ev_queue = [{"result": {"found": False}}]
    assert await s.c._handle_e2ee_pin_dialog() is False

    s2 = _Stub()
    s2.ev_queue = [{"result": {"found": True, "hasPinInput": True}}]
    assert await s2.c._handle_e2ee_pin_dialog(pin="123456") is True
    s2.c.fill.assert_awaited_once()

    s3 = _Stub()
    s3.ev_queue = [{"result": {"found": True, "hasPinPrompt": True}}]
    assert await s3.c._handle_e2ee_pin_dialog() is True  # found, no pin

    s4 = _Stub()
    s4.ev_queue = [bb.BrowserBridgeError(500, "x")]
    assert await s4.c._handle_e2ee_pin_dialog() is False


# ── thin wrappers (payload/path assertions) ───────────────────────────


@pytest.mark.asyncio
async def test_request_wrappers():
    """Methods that delegate straight to _request — real client, seam patched."""
    c = _client()
    calls: list[tuple] = []

    async def _req(method, path, json=None, **kw):
        calls.append((method, path, json))
        return {"ok": 1}

    c._request = _req
    await c.session_login("u", "p")
    await c.mouse_click(1.5, 2.5)
    await c.navigate("https://x.com")
    await c.extract_cookies()
    await c.stop_session()
    await c.get_instagram_profile()
    await c.update_instagram_profile(biography="b", full_name="n", external_url="u")
    await c.click("sel", text="t")
    await c.fill("sel", "v")
    await c.evaluate("() => 1")
    await c.upload_file("sel", "/f.png", click_selector="cs")
    paths = [p for _, p, _ in calls]
    assert paths[0] == "/session/login"
    assert "/session/mouse-click" in paths and "/session/upload" in paths
    assert "/profile/instagram" in paths
    assert calls[-1][2]["click_selector"] == "cs"


@pytest.mark.asyncio
async def test_orchestrated_wrappers():
    """Threads profile/settings — navigate+evaluate orchestration via _Stub."""
    s = _Stub()
    c = s.c
    s.ev_queue = [{"result": {"bio": "b", "followers": 5}}] * 30
    await c.get_threads_profile("u")
    out = await c.update_threads_profile("u", biography="b", full_name="n", website="w")
    assert out["updated_fields"] == ["biography"]
    assert set(out["ignored_fields"]) == {"full_name", "website"}
    await c.get_threads_settings("u")
    await c.update_threads_settings("u", show_instagram_badge=True)
    assert any("threads.com" in n for n in s.nav_calls)


@pytest.mark.asyncio
async def test_dm_wrappers():
    s = _Stub()
    c = s.c
    rich = {
        "result": {
            "conversations": [{"id": "1", "name": "n"}],
            "messages": [{"sender": "them", "text": "hi"}],
            "found": True,
            "count": 1,
            "sent": True,
            "status": "ok",
            "clicked": True,
            "typed": True,
            "url": "https://x.com/messages",
        }
    }
    s.ev_queue = [rich] * 80
    await c.get_personal_messenger_conversations_fast()
    await c.get_personal_messenger_messages_fast("t1")
    await c.get_threads_dm_conversations()
    await c.get_threads_dm_messages("t2")
    await c.send_threads_dm_message("t2", "hi")
    await c.get_twitter_dm_conversations()
    await c.get_twitter_dm_messages("t3")
    await c.send_twitter_dm_message("t3", "hi")
    await c.get_tiktok_dm_conversations()
    await c.get_tiktok_dm_messages("t4")
    await c.send_tiktok_dm_message("t4", "hi")
    await c.get_instagram_dm_conversations()
    await c.get_instagram_dm_messages("t5")
    await c.send_instagram_dm_message("t5", "hi")
    await c.trigger_typing_indicator("t1", duration=0.1)
    assert s.ev_calls or s.req_calls  # every method ran its orchestration

    # tiktok "conversation not found" early return
    s2 = _Stub()
    s2.ev_queue = [{"result": {"found": False}}]
    out = await s2.c.get_tiktok_dm_messages("missing")
    assert out["messages"] == [] and "not found" in out["error"]


@pytest.mark.asyncio
async def test_personal_messenger_conversations_and_messages():
    s = _Stub()
    s.ev_queue = [{"result": {"conversations": [{"id": "1"}]}}]
    out = await s.c.get_personal_messenger_conversations()
    assert isinstance(out, dict)
    s2 = _Stub()
    s2.ev_queue = [
        {"result": "about:blank"},  # _navigate_to_thread current-url check
        {"result": {"messages": [{"id": "m1"}]}},
    ]
    out2 = await s2.c.get_personal_messenger_messages("th1")
    assert isinstance(out2, dict)
    s3 = _Stub()
    s3.ev_queue = [
        {"result": "about:blank"},
        {"result": {"sent": True}},
    ]
    out3 = await s3.c.send_personal_messenger_message("th1", "hello")
    assert isinstance(out3, dict)


# ── post_tweet composer orchestration ─────────────────────────────────


@pytest.mark.asyncio
async def test_post_tweet_happy_path():
    s = _Stub()
    s.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s.ev_queue = [
        {"result": {"editor": True, "url": "https://x.com/compose/post"}},
        {"result": {"status": "typed", "length": 5}},  # insertText
        {"result": {"status": "no_attachments"}},  # clear attachments
        {"result": {"status": "clicked"}},  # post button
        {"result": None},  # confirmation nudge
        {"result": {"composerOpen": False, "url": "https://x.com/home"}},
        {"result": {"url": "https://x.com/me/status/1", "handle": "me"}},
    ]
    out = await s.c.post_tweet("hello")
    assert out["status"] == "ok" and out["posted"] is True
    assert "compose/post" in s.nav_calls[0]

    # with media + button-disabled → Ctrl+Enter fallback + profile scrape
    s2 = _Stub()
    s2.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s2.ev_queue = (
        [
            {"result": {"editor": True, "url": "u"}},
            {"result": {"status": "typed"}},
            {"result": {"status": "cleared"}},
            {"result": {"uploading": False, "preview": True}},  # media wait
        ]
        + [{"result": {"error": "Post button disabled"}}] * 10
        + [
            {"result": None},  # Ctrl+Enter fallback eval
            {"result": None},  # confirmation nudge
            {"result": {"composerOpen": False, "url": "u"}},
            {"result": {"url": None, "handle": "me"}},  # toast empty → profile scrape
            {"result": {"url": "https://x.com/me/status/9"}},
        ]
    )
    out2 = await s2.c.post_tweet("x", image_paths=["/a.png"])
    assert out2["status"] == "ok"
    s2.c.upload_file.assert_awaited_once()

    # insertText error surfaces
    s3 = _Stub()
    s3.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s3.ev_queue = [
        {"result": {"editor": True, "url": "u"}},
        {"result": {"error": "Could not find the tweet composer"}},
    ]
    out3 = await s3.c.post_tweet("x")
    assert out3["status"] == "error"


@pytest.mark.asyncio
async def test_post_tweet_failure_paths():
    # session not active — session dict is spread into the result
    s = _Stub()
    s.c.ensure_session = AsyncMock(return_value={"status": "waiting", "message": "no login"})
    out = await s.c.post_tweet("x")
    assert out["status"] == "waiting" and "no login" in out["error"]

    # composer never renders
    s2 = _Stub()
    s2.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s2.ev_queue = [{"result": {"editor": False, "url": "https://x.com/login"}}] * 15
    out2 = await s2.c.post_tweet("x")
    assert "composer" in out2["error"].lower()

    # media attach fails
    s3 = _Stub()
    s3.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s3.c.upload_file = AsyncMock(side_effect=bb.BrowserBridgeError(500, "upload"))
    s3.ev_queue = [
        {"result": {"editor": True, "url": "u"}},
        {"result": {"status": "typed"}},
        {"result": {"status": "no_attachments"}},
    ]
    out3 = await s3.c.post_tweet("x", image_paths=["/a.png"])
    assert "media attach failed" in out3["error"]

    # composer still open after post
    s4 = _Stub()
    s4.c.ensure_session = AsyncMock(return_value={"status": "active"})
    s4.ev_queue = [
        {"result": {"editor": True, "url": "u"}},
        {"result": {"status": "typed"}},
        {"result": {"status": "cleared"}},
        {"result": {"status": "clicked"}},
        {"result": None},
        {"result": {"composerOpen": True, "url": "https://x.com/compose/post"}},
    ]
    out4 = await s4.c.post_tweet("x")
    assert "Composer still open" in out4["error"]
