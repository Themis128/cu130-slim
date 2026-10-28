"""Unit tests for app/services/messenger_api.py — MessengerAPIClient + parser."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.messenger_api as M


def _resp(status=200, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text,
                           json=lambda: body if body is not None else {})


class _Http:
    def __init__(self, resp=None, on_request=None):
        self.resp = resp or _resp(200, {"ok": 1})
        self.on_request = on_request
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.on_request:
            return self.on_request(method, url, kw)
        return self.resp

    async def get(self, url, **kw):
        return await self._do("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._do("POST", url, **kw)

    async def delete(self, url, **kw):
        return await self._do("DELETE", url, **kw)

    async def request(self, method, url, **kw):
        return await self._do(method, url, **kw)


def _client(monkeypatch, http=None, **kw) -> M.MessengerAPIClient:
    http = http or _Http()
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: http)
    c = M.MessengerAPIClient("tok", "12345", **kw)
    c._http = http
    return c


# ── validation + init ─────────────────────────────────────────────────


def test_psid_and_init():
    assert M._validate_psid(" 123 ") == "123"
    with pytest.raises(ValueError, match="empty"):
        M._validate_psid("")
    with pytest.raises(ValueError, match="Invalid PSID"):
        M._validate_psid("abc")
    with pytest.raises(ValueError):
        M._validate_psid("1" * 33)

    with pytest.raises(ValueError, match="required"):
        M.MessengerAPIClient("", "123")
    c = M.MessengerAPIClient("tok", "12345", api_version="/v21.0")
    assert c.api_version == "v21.0" and "v21.0" in c._base_url
    with pytest.raises(ValueError):
        M.MessengerAPIClient("tok", "bad/id")

    assert c._params() == {"access_token": "tok"}
    assert c._params({"a": 1}) == {"access_token": "tok", "a": 1}
    assert c._url("/x/y").endswith("/x/y")


def test_raise_for_status():
    c = M.MessengerAPIClient("tok", "12345")
    c._raise_for_status(_resp(200), "u")  # no raise
    from app.services.facebook_api import FacebookAPIError
    with pytest.raises(FacebookAPIError):
        c._raise_for_status(_resp(400, text="bad"), "u")
    # 5xx → status normalized
    with pytest.raises(FacebookAPIError) as e:
        c._raise_for_status(_resp(503, text="x"), "u")
    assert e.value.status_code == 503
    with pytest.raises(FacebookAPIError) as e:
        c._raise_for_status(_resp(500, text="x"), "u")
    assert e.value.status_code == 502


# ── subscription + profile ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscription_and_profile(monkeypatch):
    c = _client(monkeypatch)

    await c.subscribe_page()
    m, u, kw = c._http.calls[-1]
    assert "subscribed_apps" in u
    assert "messages" in kw["params"]["subscribed_fields"]

    await c.unsubscribe_page()
    assert c._http.calls[-1][0] == "DELETE"

    c2 = _client(monkeypatch, _Http(_resp(200, {"data": [{"app": 1}]})))
    assert await c2.get_subscribed_apps() == [{"app": 1}]

    # get profile — data[0] unwrapped; empty → {}
    c2 = _client(monkeypatch, _Http(_resp(
        200, {"data": [{"greeting": [{"g": 1}]}]})))
    assert await c2.get_messenger_profile() == {"greeting": [{"g": 1}]}
    assert "greeting" in c2._http.calls[-1][2]["params"]["fields"]
    c2 = _client(monkeypatch, _Http(_resp(200, {"data": []})))
    assert await c2.get_messenger_profile(["greeting"]) == {}

    # set/delete profile
    c = _client(monkeypatch)
    await c.set_messenger_profile({"greeting": []})
    assert c._http.calls[-1][2]["json"] == {"greeting": []}
    await c.delete_messenger_profile_fields(["greeting"])
    m, u, kw = c._http.calls[-1]
    assert m == "DELETE" and kw["json"] == {"fields": ["greeting"]}

    # setup_default_profile — interpolation + whitelisted domain
    c = _client(monkeypatch)
    await c.setup_default_profile(page_name="MyPage",
                                  page_url="https://cloudless.gr/x")
    profile = c._http.calls[-1][2]["json"]
    assert "MyPage" in profile["greeting"][0]["text"]
    assert profile["whitelisted_domains"] == ["https://cloudless.gr"]
    menu_urls = str(profile["persistent_menu"])
    assert "https://cloudless.gr/x" in menu_urls
    # custom greeting text; no page_url → no whitelist key
    await c.setup_default_profile(page_name="P", greeting_text="Hi {page_name}")
    profile = c._http.calls[-1][2]["json"]
    assert profile["greeting"][0]["text"] == "Hi P"
    assert "whitelisted_domains" not in profile


# ── send API ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_methods(monkeypatch):
    c = _client(monkeypatch)

    await c.send_text("999", "hello", metadata="m1")
    body = c._http.calls[-1][2]["json"]
    assert body["message"]["text"] == "hello"
    assert body["message"]["metadata"] == "m1"
    assert body["messaging_type"] == "RESPONSE"

    # bad psid → ValueError before HTTP
    n = len(c._http.calls)
    with pytest.raises(ValueError):
        await c.send_text("bad", "x")
    assert len(c._http.calls) == n

    await c.send_attachment("999", {"type": "image", "payload": {"url": "u"}})
    assert c._http.calls[-1][2]["json"]["message"]["attachment"]["type"] == "image"

    await c.send_image_url("999", "https://img")
    att = c._http.calls[-1][2]["json"]["message"]["attachment"]
    assert att["payload"]["is_reusable"] is True

    await c.send_quick_replies("999", "pick", [{"content_type": "text"}])
    assert c._http.calls[-1][2]["json"]["message"]["quick_replies"]

    await c.send_sender_action("999", "typing_on")
    assert c._http.calls[-1][2]["json"]["sender_action"] == "typing_on"


# ── conversations + user + thread control + page info ─────────────────


@pytest.mark.asyncio
async def test_conversations_and_misc(monkeypatch):
    c = _client(monkeypatch, _Http(_resp(200, {"data": [{"id": "c1"}]})))
    out = await c.get_conversations(limit=5)
    assert out == [{"id": "c1"}]
    assert c._http.calls[-1][2]["params"]["limit"] == 5

    c = _client(monkeypatch, _Http(_resp(
        200, {"messages": {"data": [{"id": "m1"}]}})))
    assert await c.get_conversation_messages("999888") == [{"id": "m1"}]
    with pytest.raises(ValueError):
        await c.get_conversation_messages("bad/id")

    c = _client(monkeypatch)
    await c.get_user_profile("999")
    m, u, kw = c._http.calls[-1]
    assert "999" in u
    await c.pass_thread_control("999", "app1")
    assert "pass_thread_control" in c._http.calls[-1][1]
    await c.take_thread_control("999")
    assert "take_thread_control" in c._http.calls[-1][1]

    await c.get_page_info()
    assert "id,name,username" in c._http.calls[-1][2]["params"]["fields"]

    # 400 → FacebookAPIError
    from app.services.facebook_api import FacebookAPIError
    c = _client(monkeypatch, _Http(_resp(400, text="x")))
    with pytest.raises(FacebookAPIError):
        await c.get_page_info()


def test_interpolate_and_parser():
    menu = [{"url": "{page_url}", "title": "hi {page_name}"}]
    out = M._interpolate_menu(menu, page_name="P", page_url="u")
    assert out == [{"url": "u", "title": "hi P"}]
    # missing kwarg → empty string
    out = M._interpolate_menu(menu, page_name=None, page_url=None)
    assert out[0]["url"] == ""

    # parse_webhook_event — non-page object → []
    assert M.parse_webhook_event({"object": "user"}) == []

    body = {"object": "page", "entry": [{"id": "pg", "messaging": [
        # text + quick_reply → type text + payload
        {"sender": {"id": "s1"}, "recipient": {"id": "pg"},
         "timestamp": 1,
         "message": {"mid": "m1", "text": "hi",
                     "quick_reply": {"payload": "QR"}}},
        # attachment
        {"sender": {"id": "s2"}, "recipient": {"id": "pg"},
         "message": {"mid": "m2",
                     "attachments": [{"type": "image"}]}},
        # postback
        {"sender": {"id": "s3"}, "recipient": {"id": "pg"},
         "postback": {"payload": "PB"}},
        # delivery + read
        {"sender": {"id": "s4"}, "recipient": {"id": "pg"},
         "delivery": {"mids": []}},
        {"sender": {"id": "s5"}, "recipient": {"id": "pg"},
         "read": {"watermark": 1}},
        # unknown shape → dropped
        {"sender": {"id": "s6"}, "recipient": {"id": "pg"},
         "reaction": {"emoji": "x"}},
    ]}]}
    events = M.parse_webhook_event(body)
    assert len(events) == 5
    assert events[0]["message_type"] == "text"
    assert events[0]["message_text"] == "hi"
    assert events[0]["postback_payload"] == "QR"
    assert events[0]["message_id"] == "m1"
    assert events[1]["message_type"] == "attachment"
    assert events[1]["message_attachments"] == [{"type": "image"}]
    assert events[2]["message_type"] == "postback"
    assert events[2]["postback_payload"] == "PB"
    assert events[3]["message_type"] == "delivery"
    assert events[4]["message_type"] == "read"
