"""Coverage for app/services/tiktok_profile.py."""
import time
from types import SimpleNamespace

import pytest

import app.services.tiktok_profile as TP


class TestHelpers:
    def test_md5_hex(self):
        assert TP._md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72"

    def test_rbit(self):
        assert TP._rbit(0b10000000) == 0b00000001
        assert TP._rbit(0) == 0
        assert TP._rbit(0xFF) == 0xFF

    def test_hex_byte(self):
        assert TP._hex_byte(0) == "00"
        assert TP._hex_byte(255) == "ff"
        assert TP._hex_byte(0x0A) == "0a"

    def test_reverse_nibble(self):
        assert TP._reverse_nibble(0xAB) == 0xBA
        assert TP._reverse_nibble(0x00) == 0
        assert TP._reverse_nibble(0x0F) == 0xF0

    def test_sign_gorgon(self):
        sig = TP._sign_gorgon("a=1&b=2", "data", "sessionid=x")
        assert sig["X-Gorgon"].startswith("0404b0d30000")
        assert len(sig["X-Gorgon"]) == len("0404b0d30000") + 40
        assert sig["X-Khronos"].isdigit()
        assert abs(int(sig["X-Khronos"]) - int(time.time())) < 5

    def test_sign_gorgon_no_data_no_cookies(self):
        sig = TP._sign_gorgon("a=1", None, "")
        assert sig["X-Gorgon"].startswith("0404b0d30000")


class TestExceptions:
    def test_profile_error(self):
        e = TP.TikTokProfileError(404, "missing")
        assert e.status_code == 404
        assert e.detail == "missing"
        assert "404" in str(e) and "missing" in str(e)

    def test_write_blocked_default_detail(self):
        e = TP.TikTokWriteBlocked()
        assert e.status_code == 403
        assert "anti-bot" in e.detail

    def test_write_blocked_custom(self):
        e = TP.TikTokWriteBlocked("blocked!")
        assert e.detail == "blocked!"


class _HTTP:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resp

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp


def _resp(status, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text,
                           json=lambda: body or {})


class TestPrivateClient:
    def _client(self):
        return TP._TikTokPrivateClient("sess123", 999)

    def test_build_params(self):
        p = self._client()._build_params()
        assert p["iid"] == 7183409061831001857
        assert p["_rticker"] > 0 and p["ts"] > 0

    def test_headers_with_data(self):
        c = self._client()
        h = c._headers("a=1", "x=y")
        assert h["host"] == "api-h2.tiktokv.com"
        assert "X-Gorgon" in h and "X-Khronos" in h
        assert "x-ss-stub" in h
        assert h["x-ss-req-ticket"].isdigit()

    def test_headers_no_data(self):
        h = self._client()._headers("a=1", None)
        assert "x-ss-stub" not in h

    @pytest.mark.asyncio
    async def test_post_success(self, monkeypatch):
        c = _HTTP(_resp(200, {"status_code": 0}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        out = await self._client().edit_bio("new bio")
        assert out == {"status_code": 0}
        _, url, kw = c.calls[0]
        assert "commit/user" in url
        assert "signature=new+bio" in kw["data"] or "signature" in kw["data"]
        assert kw["cookies"] == {"sessionid": "sess123"}
        assert "uid=999" in kw["data"]

    @pytest.mark.asyncio
    async def test_post_403_blocked(self, monkeypatch):
        c = _HTTP(_resp(403))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(TP.TikTokWriteBlocked):
            await self._client().edit_nickname("name")

    @pytest.mark.asyncio
    async def test_post_other_error(self, monkeypatch):
        c = _HTTP(_resp(500, text="oops"))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(TP.TikTokProfileError) as ei:
            await self._client().edit_nickname("name")
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_edit_username_no_user_id(self, monkeypatch):
        c = _HTTP(_resp(200, {"ok": 1}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        await self._client().edit_username("newname")
        _, url, kw = c.calls[0]
        assert "login_name/update" in url
        assert "uid" not in kw["data"]

    @pytest.mark.asyncio
    async def test_check_username(self, monkeypatch):
        c = _HTTP(_resp(200, {"is_valid": True}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        out = await self._client().check_username("foo")
        assert out["is_valid"] is True
        method, url, kw = c.calls[0]
        assert method == "GET"
        assert "unique/id/check" in url
        assert kw["params"]["unique_id"] == "foo"

    @pytest.mark.asyncio
    async def test_get_error(self, monkeypatch):
        c = _HTTP(_resp(429, text="rate"))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(TP.TikTokProfileError) as ei:
            await self._client().check_username("x")
        assert ei.value.status_code == 429


class TestProfileService:
    def test_can_write(self):
        assert not TP.TikTokProfileService().can_write
        assert TP.TikTokProfileService(
            session_id="s", user_id=1).can_write

    @pytest.mark.asyncio
    async def test_get_profile_no_token(self):
        svc = TP.TikTokProfileService()
        with pytest.raises(TP.TikTokProfileError) as ei:
            await svc.get_profile()
        assert ei.value.status_code == 503

    @pytest.mark.asyncio
    async def test_get_profile_success(self, monkeypatch):
        svc = TP.TikTokProfileService(access_token="tok", open_id="oid")
        body = {"error": {"code": "ok"},
                "data": {"user": {
                    "open_id": "oid", "username": "cloudless",
                    "display_name": "Cloudless", "bio_description": "bio",
                    "avatar_url": "http://a", "is_verified": True,
                    "profile_deep_link": "link"}}}
        c = _HTTP(_resp(200, body))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        out = await svc.get_profile()
        assert out["username"] == "cloudless"
        assert out["is_verified"] is True
        assert out["raw"] == body
        assert "Bearer tok" in c.calls[0][2]["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_get_profile_http_error(self, monkeypatch):
        svc = TP.TikTokProfileService(access_token="tok", open_id="oid")
        c = _HTTP(_resp(401, text="unauth"))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(TP.TikTokProfileError) as ei:
            await svc.get_profile()
        assert ei.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_profile_api_error_code(self, monkeypatch):
        svc = TP.TikTokProfileService(access_token="tok", open_id="oid")
        c = _HTTP(_resp(200, {"error": {"code": "bad", "message": "nope"}}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        with pytest.raises(TP.TikTokProfileError, match="nope"):
            await svc.get_profile()

    @pytest.mark.asyncio
    async def test_get_profile_defaults(self, monkeypatch):
        svc = TP.TikTokProfileService(access_token="tok", open_id="oid")
        c = _HTTP(_resp(200, {"data": {"user": {}}}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        out = await svc.get_profile()
        assert out["is_verified"] is False and out["id"] == ""

    @pytest.mark.asyncio
    async def test_writes_require_private(self):
        svc = TP.TikTokProfileService()
        for coro in (svc.update_nickname("x"), svc.update_signature("x"),
                     svc.update_unique_id("x"), svc.check_username("x")):
            with pytest.raises(TP.TikTokProfileError) as ei:
                await coro
            assert ei.value.status_code == 503

    @pytest.mark.asyncio
    async def test_update_nickname(self, monkeypatch):
        c = _HTTP(_resp(200, {"status_code": 0}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        svc = TP.TikTokProfileService(session_id="s", user_id=1)
        out = await svc.update_nickname("NewName")
        assert out["success"] is True
        assert out["updated_fields"] == ["nickname"]
        assert "nickname=NewName" in c.calls[0][2]["data"]

    @pytest.mark.asyncio
    async def test_update_signature(self, monkeypatch):
        c = _HTTP(_resp(200, {}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        svc = TP.TikTokProfileService(session_id="s", user_id=1)
        out = await svc.update_signature("my bio")
        assert out["updated_fields"] == ["signature"]

    @pytest.mark.asyncio
    async def test_update_unique_id(self, monkeypatch):
        c = _HTTP(_resp(200, {}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        svc = TP.TikTokProfileService(session_id="s", user_id=1)
        out = await svc.update_unique_id("newhandle")
        assert out["updated_fields"] == ["unique_id"]

    @pytest.mark.asyncio
    async def test_check_username_passthrough(self, monkeypatch):
        c = _HTTP(_resp(200, {"is_valid": False}))
        monkeypatch.setattr(TP.httpx, "AsyncClient", lambda **kw: c)
        svc = TP.TikTokProfileService(session_id="s", user_id=1)
        out = await svc.check_username("taken")
        assert out["is_valid"] is False
