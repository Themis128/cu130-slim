"""Unit tests for app/services/linkedin_sidecar.py — sidecar HTTP client."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

import app.services.linkedin_sidecar as L


def _resp(status=200, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text,
                           json=lambda: body or {},
                           raise_for_status=lambda: None)


class _Http:
    """Records calls; routes by URL substring → (status, body)."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default or _resp(200, {"ok": True})
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    def _route(self, url):
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.default

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._route(url)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._route(url)


def _client(monkeypatch, http=None, **kw) -> L.LinkedInSidecarClient:
    http = http or _Http()
    monkeypatch.setattr(L.httpx, "AsyncClient", lambda *a, **k: http)
    c = L.LinkedInSidecarClient(**kw)
    c._http = http  # for test inspection
    return c


def test_init():
    c = L.LinkedInSidecarClient("http://x:1", timeout=5)
    assert c.base_url == "http://x:1" and c._timeout == 5
    # env fallback
    import os
    os.environ["LINKEDIN_BROWSER_SIDECAR_URL"] = "http://env:2"
    c = L.LinkedInSidecarClient()
    assert c.base_url == "http://env:2"


@pytest.mark.asyncio
async def test_session_and_health(monkeypatch):
    c = _client(monkeypatch)

    out = await c.health()
    assert out == {"ok": True}
    assert c._http.calls[-1][1] == "/health"

    # error → raise_for_status path only on /health; others → SidecarError
    c = _client(monkeypatch, _Http(
        routes={"/session": _resp(500, text="boom")}))
    with pytest.raises(L.LinkedInSidecarError) as e:
        await c.set_session({"s": 1})
    assert e.value.status_code == 500 and e.value.detail == "boom"

    c = _client(monkeypatch)
    await c.set_session({"cookies": []})
    assert c._http.calls[-1] == (
        "POST", "/session", {"json": {"storage_state": {"cookies": []}}})

    await c.check_session()
    assert c._http.calls[-1][1] == "/session"

    # export_cookies — cookie dict extraction + domain param
    c = _client(monkeypatch, _Http(default=_resp(
        200, {"cookies": {"li_at": "v"}})))
    out = await c.export_cookies("example.com")
    assert out == {"li_at": "v"}
    assert c._http.calls[-1] == (
        "GET", "/debug/all-cookies", {"params": {"domain": "example.com"}})

    # refresh_session — logged out → 401 error; logged in → cookies merged
    c = _client(monkeypatch, _Http(
        routes={"/debug/all-cookies": _resp(200, {"cookies": {"a": "b"}})},
        default=_resp(200, {"logged_in": False})))
    with pytest.raises(L.LinkedInSidecarError) as e:
        await c.refresh_session()
    assert e.value.status_code == 401

    c = _client(monkeypatch, _Http(
        routes={"/debug/all-cookies": _resp(200, {"cookies": {"a": "b"}})},
        default=_resp(200, {"logged_in": True, "x": 1})))
    out = await c.refresh_session()
    assert out["cookies"] == {"a": "b"} and out["logged_in"] is True

    # login — verification_code optional
    c = _client(monkeypatch)
    await c.login("u", "p")
    assert c._http.calls[-1][2]["json"] == {"username": "u", "password": "p"}
    await c.login("u", "p", verification_code="123")
    assert c._http.calls[-1][2]["json"]["verification_code"] == "123"


@pytest.mark.asyncio
async def test_profile_methods(monkeypatch):
    c = _client(monkeypatch)

    await c.get_profile()
    assert c._http.calls[-1][1] == "/profile"

    await c.get_profile_activity()
    assert "/profile/activity" in c._http.calls[-1][1] or \
        "/activity" in c._http.calls[-1][1]

    await c.update_headline("h")
    m, u, kw = c._http.calls[-1]
    assert m == "POST" and "headline" in u and kw["json"]["headline"] == "h"

    await c.update_about("a")
    assert c._http.calls[-1][2]["json"]["about"] == "a"

    await c.update_website("w")
    assert c._http.calls[-1][2]["json"]["website"] == "w"

    await c.update_location("loc")
    assert c._http.calls[-1][2]["json"]["location"] == "loc"

    # uploads — base64 payload
    await c.upload_cover(b"\x89PNG", "c.png")
    m, u, kw = c._http.calls[-1]
    assert kw["json"]["filename"] == "c.png"
    assert base64.b64decode(kw["json"]["image_base64"]) == b"\x89PNG"

    await c.upload_picture(b"img", "p.jpg")
    assert "picture" in c._http.calls[-1][1]

    # experience/education/skills payloads pass through
    c2 = _client(monkeypatch)
    await c2.add_experience(title="Dev", company="Co", start_date="2020",
                            end_date="2022", description="d", current=True)
    assert "/profile/experience" in c2._http.calls[-1][1]
    body = c2._http.calls[-1][2]["json"]
    assert body["current"] is True and body["start_date"] == "2020"

    await c2.add_education(school="U", degree="BSc", field_of_study="CS",
                           start_date="2016", end_date="2020",
                           description="d")
    assert "/profile/education" in c2._http.calls[-1][1]
    assert c2._http.calls[-1][2]["json"]["field_of_study"] == "CS"

    await c2.add_skill("Python")
    assert c2._http.calls[-1][2]["json"] == {"skill": "Python"}


@pytest.mark.asyncio
async def test_company_methods(monkeypatch):
    c = _client(monkeypatch)

    await c.get_company("acme")
    assert c._http.calls[-1] == ("GET", "/company/acme", {})

    await c.update_company_about("acme", "about")
    assert c._http.calls[-1][2]["json"] == {"about": "about"}

    await c.update_company_website("acme", "w")
    assert c._http.calls[-1][2]["json"] == {"website": "w"}

    await c.update_company_specialties("acme", ["a", "b"])
    assert c._http.calls[-1][2]["json"] == {"specialties": ["a", "b"]}

    await c.upload_company_logo("acme", b"img", "logo.jpg")
    m, u, kw = c._http.calls[-1]
    assert u == "/company/acme/logo"
    assert base64.b64decode(kw["json"]["image_base64"]) == b"img"

    await c.upload_company_cover("acme", b"img")
    assert c._http.calls[-1][1] == "/company/acme/cover"


@pytest.mark.asyncio
async def test_posting_and_messaging(monkeypatch):
    c = _client(monkeypatch)

    # post_text — visibility optional
    await c.post_text("hello")
    assert c._http.calls[-1][2]["json"] == {"message": "hello"}
    await c.post_text("hello", visibility="PUBLIC")
    assert c._http.calls[-1][2]["json"]["visibility"] == "PUBLIC"

    # post_image — optional fields
    await c.post_image([{"image_base64": "x"}])
    assert c._http.calls[-1][2]["json"] == {
        "images": [{"image_base64": "x"}]}
    await c.post_image([{"i": 1}], message="m", visibility="PUBLIC")
    body = c._http.calls[-1][2]["json"]
    assert body["message"] == "m" and body["visibility"] == "PUBLIC"

    # post_link
    await c.post_link("https://x", "msg", visibility="PUBLIC")
    body = c._http.calls[-1][2]["json"]
    assert body["url"] == "https://x" and body["visibility"] == "PUBLIC"

    # company posting
    await c.company_post_text("acme", "corp msg")
    assert c._http.calls[-1][1] == "/company/acme/post/text"
    await c.company_post_image("acme", [{"i": 1}], "m")
    assert c._http.calls[-1][1] == "/company/acme/post/image"

    # messaging
    await c.get_conversations()
    assert c._http.calls[-1][1] == "/messages"
    await c.get_thread_messages("t1")
    assert "t1" in c._http.calls[-1][1]
    await c.send_message("t1", "hi")
    m, u, kw = c._http.calls[-1]
    assert m == "POST" and "t1" in u and kw["json"]["text"] == "hi"


@pytest.mark.asyncio
async def test_error_mapping(monkeypatch):
    # every >=400 path → LinkedInSidecarError with status+detail
    c = _client(monkeypatch, _Http(default=_resp(404, text="gone")))
    calls = [
        c.check_session(), c.export_cookies(), c.login("u", "p"),
        c.get_profile(), c.get_profile_activity(),
        c.update_headline("h"), c.update_about("a"), c.update_website("w"),
        c.update_location("l"), c.upload_cover(b"i"), c.upload_picture(b"i"),
        c.add_experience(title="t", company="c"),
        c.add_education(school="s"), c.add_skill("s"),
        c.get_company("v"), c.update_company_about("v", "a"),
        c.update_company_website("v", "w"),
        c.update_company_specialties("v", []),
        c.upload_company_logo("v", b"i"), c.upload_company_cover("v", b"i"),
        c.post_text("m"), c.post_image([{"i": 1}]),
        c.post_link("https://x"), c.company_post_text("v", "m"),
        c.company_post_image("v", [{"i": 1}]),
        c.get_conversations(), c.get_thread_messages("t"),
        c.send_message("t", "x"),
    ]
    for coro in calls:
        with pytest.raises(L.LinkedInSidecarError) as e:
            await coro
        assert e.value.status_code == 404 and e.value.detail == "gone"
