"""Tests for app/api/profile.py implementation layer.

Covers the per-platform _get/_update/_upload impls (Instagram sidecar→bridge
→free fallback chain, Threads, Facebook Page Graph + personal sidecar,
LinkedIn company/person, Twitter, TikTok), the browser-novnc session
management endpoints, and the Instagram web-session set/status flow.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.api import profile as P
from app.services.browser_bridge import BrowserBridgeError
from app.services.facebook_api import FacebookAPIError
from app.services.facebook_sidecar import FacebookSidecarError
from app.services.free_instagram_client import FreeInstagramError
from app.services.instagram_private_api import InstagramPrivateAPIError
from app.services.linkedin_sidecar import LinkedInSidecarError
from app.services.tiktok_browser import TikTokBrowserError
from app.services.twitter_profile import TwitterProfileError


@pytest.fixture(autouse=True)
def _plain_crypto(monkeypatch):
    """Session ids are stored via encrypt_field/decrypt_field — identity here."""
    monkeypatch.setattr(P, "decrypt_field", lambda v: v)
    monkeypatch.setattr(P, "encrypt_field", lambda v: v)


def _acc(platform="instagram", meta=None, **kw):
    return SimpleNamespace(
        id=uuid.uuid4(),
        platform=platform,
        account_id=kw.pop("account_id", "acc1"),
        username=kw.pop("username", "cloudless.gr"),
        display_name=kw.pop("display_name", "Cloudless"),
        access_token_enc=kw.pop("access_token_enc", "enc"),
        meta_data=meta if meta is not None else {},
        **kw,
    )


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else [self._v]


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.committed = False

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    async def commit(self):
        self.committed = True


class _HTTP:
    """Fake httpx.AsyncClient context manager returning canned responses."""

    def __init__(self, resp=None, err=None):
        self._resp = resp
        self._err = err
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(("get", url))
        if self._err:
            raise self._err
        return self._resp

    async def post(self, url, **kw):
        self.calls.append(("post", url))
        if self._err:
            raise self._err
        return self._resp


def _resp(status=200, json_data=None, headers=None, text=""):
    req = httpx.Request("GET", "https://x")
    r = httpx.Response(status, json=json_data, headers=headers or {}, request=req)
    r._text = text
    return r


def _upd(**kw):
    return P.ProfileUpdateRequest(**kw)


# ── Instagram impl chain ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_instagram_via_sidecar(monkeypatch):
    client = SimpleNamespace(get_account=AsyncMock(return_value={"username": "cg", "full_name": "CG", "biography": "b", "external_url": "w"}))
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: client)
    acc = _acc(meta={"private_api_session_id": "s1"})
    out = await P._get_instagram_profile(acc)
    assert out.platform == "instagram" and out.username == "cg" and out.website == "w"


@pytest.mark.asyncio
async def test_get_instagram_bridge_fallback(monkeypatch):
    client = SimpleNamespace(get_account=AsyncMock(side_effect=InstagramPrivateAPIError(500, "down")))
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: client)
    bridge = SimpleNamespace(get_instagram_profile=AsyncMock(return_value={"username": "cg2", "is_verified": True}))
    monkeypatch.setattr(P, "_get_browser_bridge_client", lambda p: bridge)
    acc = _acc(meta={"private_api_session_id": "s1"})
    out = await P._get_instagram_profile(acc)
    assert out.username == "cg2" and out.is_verified is True


@pytest.mark.asyncio
async def test_get_instagram_free_fallback_and_503(monkeypatch):
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: SimpleNamespace(get_account=AsyncMock()))
    bridge = SimpleNamespace(get_instagram_profile=AsyncMock(side_effect=BrowserBridgeError(400, "no session")))
    monkeypatch.setattr(P, "_get_browser_bridge_client", lambda p: bridge)
    free = SimpleNamespace(get_user_by_username=AsyncMock(return_value={"username": "cg3"}))
    monkeypatch.setattr(P, "free_instagram_client", free)
    # no session_id → skips sidecar; bridge 400 → free client
    out = await P._get_instagram_profile(_acc())
    assert out.username == "cg3"
    # free fails too → 503
    free.get_user_by_username = AsyncMock(side_effect=FreeInstagramError("x"))
    with pytest.raises(HTTPException) as exc:
        await P._get_instagram_profile(_acc())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_update_instagram_field_mapping(monkeypatch):
    client = SimpleNamespace(update_account=AsyncMock())
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: client)
    acc = _acc(meta={"private_api_session_id": "s"})
    # unsupported fields → ignored; no supported → success False
    out = await P._update_instagram_profile(acc, _upd(headline="h", location="loc"), _DB())
    assert out.success is False and "headline" in out.ignored_fields
    # supported via sidecar
    out2 = await P._update_instagram_profile(acc, _upd(biography="bio", website="w"), _DB())
    assert out2.success and "biography" in out2.updated_fields
    client.update_account.assert_awaited_with("s", biography="bio", external_url="w")


@pytest.mark.asyncio
async def test_update_instagram_bridge_paths(monkeypatch):
    client = SimpleNamespace(update_account=AsyncMock(side_effect=InstagramPrivateAPIError(500, "x")))
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: client)
    acc = _acc(meta={"private_api_session_id": "s"})
    # bridge non-updated → 502
    bridge = SimpleNamespace(update_instagram_profile=AsyncMock(return_value={"status": "failed", "response": "nope"}))
    monkeypatch.setattr(P, "_get_browser_bridge_client", lambda p: bridge)
    with pytest.raises(HTTPException) as exc:
        await P._update_instagram_profile(acc, _upd(biography="b"), _DB())
    assert exc.value.status_code == 502
    # bridge 400 → 401 no-session
    bridge.update_instagram_profile = AsyncMock(side_effect=BrowserBridgeError(400, "no session"))
    with pytest.raises(HTTPException) as exc2:
        await P._update_instagram_profile(acc, _upd(biography="b"), _DB())
    assert exc2.value.status_code == 401
    # bridge updated → success
    bridge.update_instagram_profile = AsyncMock(return_value={"status": "updated", "updated_fields": ["biography"]})
    out = await P._update_instagram_profile(acc, _upd(biography="b"), _DB())
    assert out.success and out.message.endswith("(browser bridge)")


@pytest.mark.asyncio
async def test_upload_instagram_picture(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await P._upload_instagram_picture(_acc(), b"img", _DB())
    assert exc.value.status_code == 401
    client = SimpleNamespace(update_profile_picture=AsyncMock())
    monkeypatch.setattr(P, "_get_instagram_private_client", lambda: client)
    out = await P._upload_instagram_picture(_acc(meta={"private_api_session_id": "s"}), b"img", _DB())
    assert out.success and "profile_picture" in out.updated_fields
    client.update_profile_picture = AsyncMock(side_effect=InstagramPrivateAPIError(413, "too big"))
    with pytest.raises(HTTPException) as exc2:
        await P._upload_instagram_picture(_acc(meta={"private_api_session_id": "s"}), b"img", _DB())
    assert exc2.value.status_code == 413


# ── Threads impl ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_threads_profile(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await P._update_threads_profile(_acc(platform="threads", username=None), _upd())
    assert exc.value.status_code == 400
    bridge = SimpleNamespace(
        update_threads_profile=AsyncMock(return_value={"status": "updated", "updated_fields": ["biography"], "ignored_fields": ["full_name"]})
    )
    monkeypatch.setattr(P, "_get_browser_bridge_client", lambda p: bridge)
    out = await P._update_threads_profile(_acc(platform="threads"), _upd(biography="b", full_name="x", website="w"))
    assert out.success
    assert "full_name" in out.ignored_fields and "website" in out.ignored_fields
    bridge.update_threads_profile = AsyncMock(side_effect=BrowserBridgeError(502, "boom"))
    with pytest.raises(HTTPException) as exc2:
        await P._update_threads_profile(_acc(platform="threads"), _upd(biography="b"))
    assert exc2.value.status_code == 503


# ── Facebook Page impls ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_facebook_page_profile(monkeypatch):
    monkeypatch.setattr(P, "decrypt_token", lambda t: "tok")
    client = SimpleNamespace(get_page_info=AsyncMock(return_value={"name": "cloudless.gr", "about": "a", "website": "w", "picture": {"data": {"url": "pic"}}}))
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: client)
    out = await P._get_facebook_page_profile(_acc(platform="facebook"))
    assert out.full_name == "cloudless.gr" and out.profile_pic_url == "pic"
    client.get_page_info = AsyncMock(side_effect=FacebookAPIError(400, "bad", "url"))
    with pytest.raises(HTTPException) as exc:
        await P._get_facebook_page_profile(_acc(platform="facebook"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_facebook_page_update_and_uploads(monkeypatch):
    monkeypatch.setattr(P, "decrypt_token", lambda t: "tok")
    client = SimpleNamespace(update_page_info=AsyncMock(), upload_profile_picture=AsyncMock(), upload_cover_photo=AsyncMock())
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: client)
    acc = _acc(platform="facebook")
    out = await P._update_facebook_page_profile(acc, _upd(about="a", website="w", headline="ignored-field"))
    assert out.success and "about" in out.updated_fields
    assert "headline" in out.ignored_fields
    out2 = await P._update_facebook_page_profile(acc, _upd(email="e"))
    assert out2.success is False and "email" in out2.ignored_fields
    out3 = await P._upload_facebook_page_picture(acc, b"i")
    out4 = await P._upload_facebook_page_cover(acc, b"c")
    assert out3.success and out4.success
    client.upload_cover_photo = AsyncMock(side_effect=FacebookAPIError(403, "denied", "url"))
    with pytest.raises(HTTPException):
        await P._upload_facebook_page_cover(acc, b"c")


# ── Facebook personal (sidecar) impls ─────────────────────────────────


@pytest.mark.asyncio
async def test_facebook_sidecar_helpers(monkeypatch):
    acc = _acc(platform="facebook")
    with pytest.raises(HTTPException) as exc:
        P._get_facebook_browser_storage(acc)
    assert exc.value.status_code == 401
    storage = {"cookies": []}
    acc2 = _acc(platform="facebook", meta={"browser_storage_state": storage})
    client = SimpleNamespace(set_session=AsyncMock())
    monkeypatch.setattr(P, "FacebookSidecarClient", lambda: client)
    assert await P._get_facebook_sidecar(acc2) is client
    client.set_session.assert_awaited_with(storage)
    client.set_session = AsyncMock(side_effect=FacebookSidecarError(401, "expired"))
    with pytest.raises(HTTPException) as exc2:
        await P._get_facebook_sidecar(acc2)
    assert exc2.value.status_code == 401


@pytest.mark.asyncio
async def test_facebook_user_profile(monkeypatch):
    client = SimpleNamespace(get_profile=AsyncMock(return_value={"profile": {"name": "Themis", "bio": "b", "website": "w"}}))
    monkeypatch.setattr(P, "_get_facebook_sidecar", AsyncMock(return_value=client))
    out = await P._get_facebook_user_profile(_acc(platform="facebook"))
    assert out.full_name == "Themis" and out.about == "b"
    client.get_profile = AsyncMock(side_effect=FacebookSidecarError(500, "x"))
    with pytest.raises(HTTPException):
        await P._get_facebook_user_profile(_acc(platform="facebook"))


@pytest.mark.asyncio
async def test_facebook_user_update(monkeypatch):
    client = SimpleNamespace(update_bio=AsyncMock(), update_website=AsyncMock(), update_name=AsyncMock())
    monkeypatch.setattr(P, "_get_facebook_sidecar", AsyncMock(return_value=client))
    out = await P._update_facebook_user_profile(_acc(platform="facebook"), _upd(about="a", website="w"))
    assert out.success and "about" in out.updated_fields and "website" in out.updated_fields


# ── LinkedIn impls ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linkedin_helpers(monkeypatch):
    acc = _acc(platform="linkedin")
    with pytest.raises(HTTPException):
        P._get_linkedin_browser_storage(acc)
    acc2 = _acc(platform="linkedin", meta={"browser_storage_state": {"c": 1}})
    client = SimpleNamespace(set_session=AsyncMock())
    monkeypatch.setattr(P, "LinkedInSidecarClient", lambda: client)
    assert await P._get_linkedin_sidecar(acc2) is client
    assert P._is_linkedin_company(_acc(platform="linkedin", username="cloudless-gr"))
    assert not P._is_linkedin_company(_acc(platform="linkedin", username="me@x.com"))


@pytest.mark.asyncio
async def test_linkedin_get_profile_company_and_person(monkeypatch):
    client = SimpleNamespace(
        get_company=AsyncMock(return_value={"company": {"name": "Cloudless", "tagline": "t", "website": "w", "logo_url": "l"}}),
        get_profile=AsyncMock(return_value={"profile": {"name": "T", "headline": "h"}}),
    )
    monkeypatch.setattr(P, "_get_linkedin_sidecar", AsyncMock(return_value=client))
    out = await P._get_linkedin_profile(_acc(platform="linkedin", username="cloudless-gr"))
    assert out.full_name == "Cloudless" and out.headline == "t" and out.profile_pic_url == "l"
    out2 = await P._get_linkedin_profile(_acc(platform="linkedin", username="me@x.com"))
    assert out2.headline == "h"
    client.get_company = AsyncMock(side_effect=LinkedInSidecarError(502, "x"))
    with pytest.raises(HTTPException):
        await P._get_linkedin_profile(_acc(platform="linkedin", username="cloudless-gr"))


@pytest.mark.asyncio
async def test_linkedin_update_person_and_company(monkeypatch):
    client = SimpleNamespace(
        update_headline=AsyncMock(),
        update_about=AsyncMock(),
        update_website=AsyncMock(),
        update_location=AsyncMock(),
        add_experience=AsyncMock(),
        add_education=AsyncMock(),
        update_company_about=AsyncMock(),
        update_company_website=AsyncMock(),
    )
    monkeypatch.setattr(P, "_get_linkedin_sidecar", AsyncMock(return_value=client))
    person = _acc(platform="linkedin", username="me@x.com")
    out = await P._update_linkedin_profile(
        person,
        _upd(
            headline="h", about="a", location="l", work=[P.WorkEntry(employer="e", position="p")], education=[P.EducationEntry(school="s")], biography="ignored"
        ),
    )
    assert out.success and "work" in out.updated_fields and "education" in out.updated_fields
    assert "biography" in out.ignored_fields
    client.add_experience.assert_awaited_with(title="p", company="e", start_date=None, end_date=None, description=None)
    # no fields → fail soft
    out2 = await P._update_linkedin_profile(person, _upd(phone="p"))
    assert out2.success is False
    # company path
    out3 = await P._update_linkedin_profile(_acc(platform="linkedin", username="cloudless-gr"), _upd(about="a", website="w", biography="bio"))
    assert out3.success and "about" in out3.updated_fields
    assert "biography" in out3.ignored_fields
    client.update_company_about.assert_awaited_with("cloudless-gr", "a")


@pytest.mark.asyncio
async def test_linkedin_uploads(monkeypatch):
    client = SimpleNamespace(upload_picture=AsyncMock(), upload_cover=AsyncMock())
    monkeypatch.setattr(P, "_get_linkedin_sidecar", AsyncMock(return_value=client))
    acc = _acc(platform="linkedin")
    assert (await P._upload_linkedin_picture(acc, b"i")).success
    assert (await P._upload_linkedin_cover(acc, b"c")).success
    client.upload_picture = AsyncMock(side_effect=LinkedInSidecarError(500, "x"))
    with pytest.raises(HTTPException):
        await P._upload_linkedin_picture(acc, b"i")


# ── Twitter impls ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_twitter_service_creds(monkeypatch):
    store = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(P, "secret_store", store)
    monkeypatch.setattr(P.settings, "TWITTER_API_KEY", "")
    monkeypatch.setattr(P.settings, "TWITTER_API_SECRET", "")
    monkeypatch.setattr(P.settings, "TWITTER_ACCESS_TOKEN", "")
    monkeypatch.setattr(P.settings, "TWITTER_ACCESS_TOKEN_SECRET", "")
    with pytest.raises(HTTPException) as exc:
        await P._get_twitter_service()
    assert exc.value.status_code == 503
    svc = object()
    monkeypatch.setattr(P, "TwitterProfileService", lambda **kw: svc)
    monkeypatch.setattr(P.settings, "TWITTER_API_KEY", "k")
    monkeypatch.setattr(P.settings, "TWITTER_ACCESS_TOKEN", "t")
    assert await P._get_twitter_service() is svc


@pytest.mark.asyncio
async def test_twitter_profile_and_update(monkeypatch):
    svc = SimpleNamespace(
        get_profile=lambda: {"username": "u", "full_name": "n", "followers": 42},
        update_profile=lambda **kw: {"updated_fields": list(kw.keys())},
        update_profile_image=lambda b: None,
        update_profile_banner=lambda b: None,
    )
    monkeypatch.setattr(P, "_get_twitter_service", AsyncMock(return_value=svc))
    acc = _acc(platform="twitter")
    out = await P._get_twitter_profile(acc)
    assert out.username == "u" and out.followers == 42
    out2 = await P._update_twitter_profile(acc, _upd(full_name="n", about="a", website="w", phone="p"))
    assert out2.success and set(out2.updated_fields) == {"name", "description", "url"}
    assert "phone" in out2.ignored_fields
    assert (await P._update_twitter_profile(acc, _upd())).success is False
    assert (await P._upload_twitter_picture(acc, b"i")).success
    assert (await P._upload_twitter_banner(acc, b"b")).success
    svc.get_profile = lambda: (_ for _ in ()).throw(TwitterProfileError(429, "rate"))
    with pytest.raises(HTTPException) as exc:
        await P._get_twitter_profile(acc)
    assert exc.value.status_code == 429


# ── TikTok impls ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tiktok_profile_and_update(monkeypatch):
    monkeypatch.setattr(P, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(P, "secret_store", SimpleNamespace(get=AsyncMock(return_value="123")))
    svc = SimpleNamespace(
        can_write=True, get_profile=AsyncMock(return_value={"username": "cg", "is_verified": True}), update_nickname=AsyncMock(), update_signature=AsyncMock()
    )
    monkeypatch.setattr(P, "TikTokProfileService", lambda **kw: svc)
    acc = _acc(platform="tiktok")
    out = await P._get_tiktok_profile(acc)
    assert out.username == "cg" and out.is_verified is True
    out2 = await P._update_tiktok_profile(acc, _upd(full_name="n", about="a", website="w"))
    assert out2.success and "nickname" in out2.updated_fields
    assert "website" in out2.ignored_fields
    svc.can_write = False
    with pytest.raises(HTTPException) as exc:
        await P._update_tiktok_profile(acc, _upd(full_name="n"))
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc2:
        await P._upload_tiktok_picture(acc, b"i")
    assert exc2.value.status_code == 501


@pytest.mark.asyncio
async def test_tiktok_browser_settings_endpoints(monkeypatch):
    svc = SimpleNamespace(
        close=AsyncMock(),
        set_session=AsyncMock(return_value={"ok": True}),
        check_session=AsyncMock(return_value={"logged_in": True}),
        read_all_settings=AsyncMock(return_value={"privacy": "private"}),
        set_private_account=AsyncMock(return_value={"ok": True}),
        set_comments=AsyncMock(return_value={"ok": True}),
        set_direct_messages=AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(P, "_get_tiktok_browser_service", lambda: svc)
    u = SimpleNamespace(id=1)
    assert (await P.set_tiktok_browser_session(P.TikTokSessionRequest(session_id="s"), current_user=u))["ok"]
    assert (await P.check_tiktok_browser_session(current_user=u))["logged_in"]
    assert (await P.get_tiktok_all_settings(current_user=u))["privacy"] == "private"
    assert (await P.set_tiktok_private_account(P.TikTokToggleRequest(enabled=True), current_user=u))["ok"]
    assert (await P.set_tiktok_comments(P.TikTokCommentsRequest(permission="Friends"), current_user=u))["ok"]
    svc.check_session = AsyncMock(side_effect=TikTokBrowserError(503, "down"))
    with pytest.raises(HTTPException):
        await P.check_tiktok_browser_session(current_user=u)


# ── browser-novnc session endpoints ───────────────────────────────────


@pytest.mark.asyncio
async def test_browser_session_lifecycle(monkeypatch):
    u = SimpleNamespace(id=1)
    good = _resp(200, {"platform": "instagram", "status": "started", "message": "m", "novnc_url": "http://vnc"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(resp=good))
    out = await P.start_browser_session(P.BrowserSessionRequest(platform="instagram"), current_user=u)
    assert out.status == "started" and out.novnc_url == "http://vnc"
    # connect error → 503 on every endpoint
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(err=httpx.ConnectError("x")))
    for ep in (
        P.browser_session_status,
        P.browser_session_cookies,
        P.stop_browser_session,
        P.extract_browser_cookies,
        P.browser_novnc_url,
        P.browser_platforms,
    ):
        with pytest.raises(HTTPException) as exc:
            await ep(current_user=u)
        assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc2:
        await P.start_browser_session(P.BrowserSessionRequest(platform="x"), current_user=u)
    assert exc2.value.status_code == 503


@pytest.mark.asyncio
async def test_browser_http_status_error(monkeypatch):
    err_resp = _resp(404, {"detail": "no session"})
    err_resp.status_code = 404

    class _ErrHTTP(_HTTP):
        async def post(self, url, **kw):
            raise httpx.HTTPStatusError("e", request=err_resp.request, response=err_resp)

        async def get(self, url, **kw):
            raise httpx.HTTPStatusError("e", request=err_resp.request, response=err_resp)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _ErrHTTP())
    u = SimpleNamespace(id=1)
    with pytest.raises(HTTPException) as exc:
        await P.start_browser_session(P.BrowserSessionRequest(platform="x"), current_user=u)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc2:
        await P.browser_session_cookies(current_user=u)
    assert exc2.value.detail == "no session"


@pytest.mark.asyncio
async def test_import_instagram_session(monkeypatch):
    u = SimpleNamespace(id=1)
    # cookies endpoint returns no sessionid → 400
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(resp=_resp(200, {"cookies": {}})))
    with pytest.raises(HTTPException) as exc:
        await P.import_instagram_session_from_browser(current_user=u, db=_DB())
    assert exc.value.status_code == 400
    # full flow: cookies → sidecar → db account save
    cookies = _resp(200, {"cookies": {"sessionid": "sid1234567890abcdef"}})
    sidecar = _resp(200, {"session_id": "new-sid"}, {"content-type": "application/json"})
    seq = [cookies, sidecar]

    class _Seq(_HTTP):
        def _next(self):
            return seq.pop(0)

        async def get(self, url, **kw):
            return self._next()

        async def post(self, url, **kw):
            return self._next()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Seq())
    monkeypatch.setattr(P, "flag_modified", lambda *a: None)
    acc = _acc(platform="instagram")
    db = _DB(results=[[acc]])
    out = await P.import_instagram_session_from_browser(current_user=u, db=db)
    assert out["success"] and db.committed
    assert acc.meta_data["instagram_sessionid_cookie"] == "sid1234567890abcdef"


@pytest.mark.asyncio
async def test_instagram_web_session(monkeypatch):
    u = SimpleNamespace(id=1)
    monkeypatch.setattr(P, "flag_modified", lambda *a: None)
    body = P.InstagramWebSessionRequest(sessionid="sid", csrftoken="csrf", ds_user_id="42")
    # no account → 404
    with pytest.raises(HTTPException):
        await P.set_instagram_web_session(body, current_user=u, db=_DB(results=[[]]))
    acc = _acc(platform="instagram")
    db = _DB(results=[[acc]])
    out = await P.set_instagram_web_session(body, current_user=u, db=db)
    assert out["success"] and acc.meta_data["private_api_csrf_token"] == "csrf"
    # status: not configured
    acc2 = _acc(platform="instagram")
    out2 = await P.get_instagram_web_session_status(current_user=u, db=_DB(results=[[acc2]]))
    assert out2["configured"] is False
    # status: configured + valid
    acc3 = _acc(platform="instagram", meta={"private_api_session_id": "sid", "private_api_csrf_token": "c", "private_api_ds_user_id": "42"})
    good = _resp(200, {"user": {"username": "cg"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(resp=good))
    out3 = await P.get_instagram_web_session_status(current_user=u, db=_DB(results=[[acc3]]))
    assert out3["valid"] is True and out3["username"] == "cg"
    # status: expired session (302 / deleted cookie)
    expired = _resp(302, headers={"set-cookie": "sessionid=deleted"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(resp=expired))
    out4 = await P.get_instagram_web_session_status(current_user=u, db=_DB(results=[[acc3]]))
    assert out4["valid"] is False and "expired" in out4["message"].lower()
    # status: network fail
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _HTTP(err=httpx.ConnectError("x")))
    out5 = await P.get_instagram_web_session_status(current_user=u, db=_DB(results=[[acc3]]))
    assert out5["valid"] is False


# ── TikTok browser-settings endpoints (1556-1790) ────────────────────


class _TTSvc:
    """Fake TikTokBrowserService — records calls, close() is async."""

    def __init__(self, raise_err=False):
        self.closed = 0
        self.calls = []
        self.raise_err = raise_err

    async def close(self):
        self.closed += 1

    def __getattr__(self, name):
        async def _m(*a, **kw):
            self.calls.append((name, a, kw))
            if self.raise_err:
                raise TikTokBrowserError(503, "sidecar down")
            return {"status": "ok", "op": name}

        return _m


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,req_factory,svc_method",
    [
        (P.set_tiktok_browser_session, lambda: P.TikTokSessionRequest(session_id="sid", user_id="u"), "set_session"),
        (P.set_tiktok_private_account, lambda: P.TikTokToggleRequest(enabled=True), "set_private_account"),
        (P.set_tiktok_comments, lambda: P.TikTokCommentsRequest(permission="Friends"), "set_comments"),
        (P.set_tiktok_direct_messages, lambda: P.TikTokDirectMessagesRequest(potential_connections="Friends", others="No one"), "set_direct_messages"),
        (P.set_tiktok_desktop_notifications, lambda: P.TikTokToggleRequest(enabled=False), "set_desktop_notifications"),
        (P.set_tiktok_interaction_notifications, lambda: P.TikTokInteractionNotificationsRequest(likes=True, comments=False), "set_interaction_notifications"),
        (P.set_tiktok_personalized_ads, lambda: P.TikTokToggleRequest(enabled=False), "set_personalized_ads"),
        (P.set_tiktok_color_contrast, lambda: P.TikTokToggleRequest(enabled=True), "set_color_contrast"),
        (
            P.fill_tiktok_business_verification,
            lambda: P.TikTokBusinessVerificationRequest(company_name="Cloudless", website="https://cloudless.gr"),
            "fill_business_verification",
        ),
    ],
)
async def test_tiktok_settings_endpoints(monkeypatch, endpoint, req_factory, svc_method):
    svc = _TTSvc()
    monkeypatch.setattr(P, "_get_tiktok_browser_service", lambda: svc)
    out = await endpoint(req=req_factory(), current_user=object())
    assert out["status"] == "ok"
    assert svc.calls[0][0] == svc_method
    assert svc.closed == 1

    # error path → HTTPException with the sidecar's status, close still runs
    svc2 = _TTSvc(raise_err=True)
    monkeypatch.setattr(P, "_get_tiktok_browser_service", lambda: svc2)
    with pytest.raises(HTTPException) as exc:
        await endpoint(req=req_factory(), current_user=object())
    assert exc.value.status_code == 503 and svc2.closed == 1


@pytest.mark.asyncio
async def test_tiktok_get_endpoints(monkeypatch):
    for ep, meth in (
        (P.check_tiktok_browser_session, "check_session"),
        (P.get_tiktok_all_settings, "read_all_settings"),
        (P.get_tiktok_business_verification_status, "get_business_verification_status"),
    ):
        svc = _TTSvc()
        monkeypatch.setattr(P, "_get_tiktok_browser_service", lambda: svc)
        out = await ep(current_user=object())
        assert out["status"] == "ok" and svc.calls[0][0] == meth
        assert svc.closed == 1

        svc2 = _TTSvc(raise_err=True)
        monkeypatch.setattr(P, "_get_tiktok_browser_service", lambda: svc2)
        with pytest.raises(HTTPException):
            await ep(current_user=object())
        assert svc2.closed == 1


# ── Facebook personal profile field matrix + uploads ────────────────


@pytest.mark.asyncio
async def test_facebook_user_update_all_fields(monkeypatch):
    client = SimpleNamespace(
        update_bio=AsyncMock(),
        update_website=AsyncMock(),
        update_quotes=AsyncMock(),
        update_location=AsyncMock(),
        update_contact=AsyncMock(),
        update_work=AsyncMock(),
        update_education=AsyncMock(),
    )
    monkeypatch.setattr(P, "_get_facebook_sidecar", AsyncMock(return_value=client))
    upd = _upd(
        about="a",
        website="w",
        quotes="q",
        location="Athens",
        phone="123",
        email="e@x.io",
        work=[P.WorkEntry(employer="ACME", position="CEO", description="d")],
        education=[P.EducationEntry(school="Uni", degree="BSc")],
        headline="ignored-h",
        biography="ignored-b",
        full_name="ignored-n",
    )
    out = await P._update_facebook_user_profile(_acc(platform="facebook"), upd)
    assert out.success
    for f in ("about", "website", "quotes", "location", "phone", "email", "work", "education"):
        assert f in out.updated_fields
    client.update_work.assert_awaited_once_with(company="ACME", position="CEO", description="d")
    client.update_education.assert_awaited_once_with(school="Uni", degree="BSc")
    for f in ("headline", "biography", "full_name"):
        assert f in out.ignored_fields


@pytest.mark.asyncio
async def test_facebook_user_update_error_and_empty(monkeypatch):
    client = SimpleNamespace(update_bio=AsyncMock(side_effect=FacebookSidecarError(401, "expired")))
    monkeypatch.setattr(P, "_get_facebook_sidecar", AsyncMock(return_value=client))
    with pytest.raises(HTTPException) as e:
        await P._update_facebook_user_profile(_acc(platform="facebook"), _upd(about="a"))
    assert e.value.status_code == 401

    # Nothing updatable → success=False with ignored list
    out = await P._update_facebook_user_profile(_acc(platform="facebook"), _upd(headline="h"))
    assert not out.success and "headline" in out.ignored_fields


@pytest.mark.asyncio
async def test_facebook_user_picture_and_cover(monkeypatch):
    client = SimpleNamespace(upload_picture=AsyncMock(), upload_cover=AsyncMock())
    monkeypatch.setattr(P, "_get_facebook_sidecar", AsyncMock(return_value=client))
    out = await P._upload_facebook_user_picture(_acc(platform="facebook"), b"img")
    assert out.success and "profile_picture" in out.updated_fields
    client.upload_picture.assert_awaited_once_with(b"img")

    out = await P._upload_facebook_user_cover(_acc(platform="facebook"), b"cov")
    assert out.success and "cover" in out.updated_fields

    client.upload_cover = AsyncMock(side_effect=FacebookSidecarError(500, "down"))
    with pytest.raises(HTTPException):
        await P._upload_facebook_user_cover(_acc(platform="facebook"), b"c")


# ── import-instagram-session-from-browser ────────────────────────────


def _route_http(get_resp=None, post_resp=None, get_err=None, post_err=None):
    """Two-phase client: bridge cookies GET then sidecar sessionid POST."""

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            if get_err:
                raise get_err
            return get_resp or _resp(404)

        async def post(self, url, **kw):
            if post_err:
                raise post_err
            return post_resp or _resp(404)

    return _C


@pytest.mark.asyncio
async def test_import_ig_session_happy(monkeypatch):
    client_cls = _route_http(
        get_resp=_resp(200, {"cookies": {"sessionid": "raw-sid"}}),
        post_resp=_resp(200, {"session_id": "ss-sidecar-12345678901234567890"}, headers={"content-type": "application/json"}),
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    acct = _acc(platform="instagram", meta={})
    db = _DB([[acct]])
    monkeypatch.setattr(P, "flag_modified", lambda *a: None)
    out = await P.import_instagram_session_from_browser(current_user=None, db=db)
    assert out["success"] and out["session_id"].endswith("...")
    assert acct.meta_data["private_api_session_id"] == "ss-sidecar-12345678901234567890"
    assert acct.meta_data["instagram_sessionid_cookie"] == "raw-sid"
    assert db.committed


@pytest.mark.asyncio
async def test_import_ig_session_no_cookies(monkeypatch):
    client_cls = _route_http(get_resp=_resp(200, {"cookies": {}}))
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    with pytest.raises(HTTPException) as e:
        await P.import_instagram_session_from_browser(current_user=None, db=_DB())
    assert e.value.status_code == 400
    assert "sessionid" in e.value.detail


@pytest.mark.asyncio
async def test_import_ig_session_bridge_down(monkeypatch):
    client_cls = _route_http(get_err=httpx.ConnectError("down"))
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    with pytest.raises(HTTPException) as e:
        await P.import_instagram_session_from_browser(current_user=None, db=_DB())
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_import_ig_session_sidecar_rejects(monkeypatch):
    client_cls = _route_http(
        get_resp=_resp(200, {"cookies": {"sessionid": "raw-sid"}}),
        post_resp=_resp(400, text="bad session"),
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    with pytest.raises(HTTPException) as e:
        await P.import_instagram_session_from_browser(current_user=None, db=_DB())
    assert e.value.status_code == 400
    assert "rejected" in e.value.detail

    # Sidecar unreachable → 503
    client_cls = _route_http(
        get_resp=_resp(200, {"cookies": {"sessionid": "raw-sid"}}),
        post_err=httpx.ConnectError("down"),
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    with pytest.raises(HTTPException) as e:
        await P.import_instagram_session_from_browser(current_user=None, db=_DB())
    assert e.value.status_code == 503
