"""Unit tests for app/services/instagram_private_api.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.services.instagram_private_api as IP


class _Resp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _AC:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _rec(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self.response

    async def post(self, url, **kw):
        return await self._rec("post", url, **kw)

    async def get(self, url, **kw):
        return await self._rec("get", url, **kw)

    async def patch(self, url, **kw):
        return await self._rec("patch", url, **kw)


def _wire(resp):
    fake = _AC(resp)
    return patch.object(IP.httpx, "AsyncClient", return_value=fake), fake


def _client():
    return IP.InstagramPrivateAPIClient("http://side/")


def test_headers_and_error():
    c = _client()
    assert c._headers() == {"Accept": "application/json"}
    assert c._headers("s1")["X-Session-ID"] == "s1"

    err = IP.InstagramPrivateAPIError(403, "denied")
    assert err.status_code == 403 and "denied" in str(err)

    # error detail from json
    resp = _Resp(400, {"detail": "bad request"})
    with pytest.raises(IP.InstagramPrivateAPIError, match="bad request"):
        c._raise_for_status(resp)
    # error detail fallback to text on bad json
    resp = _Resp(500, ValueError("x"), text="raw text")
    with pytest.raises(IP.InstagramPrivateAPIError, match="raw text"):
        c._raise_for_status(resp)
    # success: json parse fail → {}
    resp = _Resp(200, ValueError("x"))
    assert c._raise_for_status(resp) == {}


# ── auth ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_shapes():
    c = _client()
    # string body → session_id
    w, fake = _wire(_Resp(200, "sess-abc"))
    with w:
        out = await c.login("u", "p", verification_code="123456",
                            proxy="http://p")
    assert out == {"session_id": "sess-abc", "logged_in": True}
    assert fake.calls[0]["data"]["verification_code"] == "123456"
    assert fake.calls[0]["data"]["proxy"] == "http://p"

    # bool body → session id from header
    w, _ = _wire(_Resp(200, True, headers={"x-session-id": "hdr"}))
    with w:
        out = await c.login("u", "p")
    assert out == {"session_id": "hdr", "logged_in": True}

    # dict body passthrough
    w, _ = _wire(_Resp(200, {"logged_in": True, "user": {}}))
    with w:
        assert (await c.login("u", "p"))["logged_in"] is True

    # unparseable body → {}
    w, _ = _wire(_Resp(200, ValueError("x")))
    with w:
        assert await c.login("u", "p") == {}


@pytest.mark.asyncio
async def test_login_challenge_and_2fa():
    c = _client()
    w, _ = _wire(_Resp(400, {"exc_type": "ChallengeRequired",
                            "hint": "check your phone",
                            "last_json": '{"a":1}'}))
    with w:
        out = await c.login("u", "p")
    assert out["challenge_required"] is True
    assert out["two_factor_required"] is False
    assert out["message"] == "check your phone"

    w, _ = _wire(_Resp(400, {"exc_type": "TwoFactorRequired"}))
    with w:
        out = await c.login("u", "p")
    assert out["two_factor_required"] is True

    # unknown error → raise
    w, _ = _wire(_Resp(400, {"detail": "bad creds"}))
    with w:
        with pytest.raises(IP.InstagramPrivateAPIError, match="bad creds"):
            await c.login("u", "p")

    # unparseable error body → detail from text
    w, _ = _wire(_Resp(500, ValueError("x"), text="oops"))
    with w:
        with pytest.raises(IP.InstagramPrivateAPIError, match="oops"):
            await c.login("u", "p")


@pytest.mark.asyncio
async def test_login_by_sessionid_and_challenge():
    c = _client()
    w, fake = _wire(_Resp(200, {"session_id": "s"}))
    with w:
        out = await c.login_by_sessionid("cookie", proxy="http://p")
    assert out["session_id"] == "s"
    assert "by/sessionid" in fake.calls[0]["url"]
    assert fake.calls[0]["data"]["proxy"] == "http://p"

    w, fake = _wire(_Resp(200, {"ok": 1}))
    with w:
        out = await c.challenge_resolve("s1", {"nested": 1}, "999")
    assert out == {"ok": 1}
    assert fake.calls[0]["data"]["security_code"] == "999"
    assert fake.calls[0]["headers"]["X-Session-ID"] == "s1"


@pytest.mark.asyncio
async def test_settings_roundtrip():
    c = _client()
    w, fake = _wire(_Resp(200, {"settings": {}}))
    with w:
        await c.get_settings("s1")
        await c.set_settings("{}", proxy="p", locale="el",
                             timezone="UTC")
    assert fake.calls[0]["method"] == "get"
    assert "/auth/settings" in fake.calls[0]["url"]
    assert fake.calls[1]["method"] == "patch"
    assert fake.calls[1]["data"]["locale"] == "el"


# ── account profile ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_reads_writes():
    c = _client()
    w, fake = _wire(_Resp(200, {"ok": 1}))
    with w:
        await c.get_account("s")
        await c.update_account("s", biography="b", email="e@x",
                               username="u2", external_url="https://u",
                               full_name="F", phone_number="30")
        await c.update_biography("s", "bio")
        await c.update_external_url("s", "https://x")
        await c.update_profile_picture("s", b"\xff\xd8", "p.jpg")
        await c.set_private("s")
        await c.set_public("s")
    paths = [c_["url"].split("side/")[1] for c_ in fake.calls]
    assert paths == ["account", "account", "account/biography",
                     "account/external-url", "account/picture",
                     "account/privacy", "account/privacy"]
    upd = fake.calls[1]
    assert upd["data"] == {"biography": "b", "username": "u2",
                           "email": "e@x", "external_url": "https://u",
                           "full_name": "F", "phone_number": "30"}
    assert fake.calls[5]["data"] == {"private": "true"}
    assert fake.calls[6]["data"] == {"private": "false"}
    assert fake.calls[4]["files"]["picture"][0] == "p.jpg"


@pytest.mark.asyncio
async def test_update_account_requires_field():
    with pytest.raises(ValueError, match="At least one field"):
        await _client().update_account("s")


# ── uploads ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uploads(tmp_path):
    c = _client()
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"thumb")

    w, fake = _wire(_Resp(200, {"id": "m1"}))
    with w:
        out = await c.upload_photo("s", str(photo), "cap",
                                   location="Athens")
        assert out["id"] == "m1"
        call = fake.calls[0]
        assert "/photo/upload" in call["url"]
        assert call["data"]["location"] == "Athens"
        assert call["files"]["file"][0] == "p.jpg"

        await c.upload_photo_by_url("s", "https://x/i.jpg", "cap")
        assert fake.calls[1]["data"]["url"] == "https://x/i.jpg"

        await c.upload_video("s", str(video), "cap",
                             thumbnail=str(thumb))
        call = fake.calls[2]
        assert "/video/upload" in call["url"]
        assert "thumbnail" in call["files"]

        await c.upload_video("s", str(video), "cap", location="X")
        assert "thumbnail" not in fake.calls[3]["files"]
        assert fake.calls[3]["data"]["location"] == "X"

        await c.upload_video_by_url("s", "https://x/v.mp4", "cap",
                                    thumbnail="https://x/t.jpg")
        assert fake.calls[4]["data"]["thumbnail"] == "https://x/t.jpg"

        await c.upload_album("s", [str(photo), str(video)], "cap",
                             location="Y")
        call = fake.calls[5]
        assert "/album/upload" in call["url"]
        assert len(call["files"]) == 2
        assert call["data"]["location"] == "Y"

        await c.upload_story("s", str(photo), "cap", as_video=True)
        call = fake.calls[6]
        assert call["data"]["caption"] == "cap"
        assert call["data"]["as_video"] == "true"
