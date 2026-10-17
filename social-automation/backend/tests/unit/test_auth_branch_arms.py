"""Remaining reachable branch arms in app/api/auth.py.

- LinkedIn org ACL fallback across endpoints, and org names that are
  blank strings or empty localized dicts
- a legacy refresh token with no jti and a user with no team
- an export token whose team claim the user isn't a member of
- a non-PKCE authorize (no verifier in state)
- page-backed / Business Manager IG entries with no id
- WhatsApp connects where no business has a WABA, or the phone lookup
  fails
- the Messenger alias callback
- a TikTok re-grant that keeps scope-gated meta
- an owner who opted out of connect emails
- an IG2 long-lived token with no expiry
- a Meta data-deletion without a page_id

Reuses the in-memory fakes from test_auth_api.py; no network, no DB."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from app.api import auth
from app.core.security import create_access_token, decode_token, encrypt_token, sign_oauth_state, verify_oauth_state
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.services.meta_graph import facebook_graph_url as fbu
from tests.unit.test_auth_api import (  # noqa: F401
    FakeDB,
    TestInstagram2Callback,
    TestMetaDataDeletion,
    _FakeAsyncHTTPClient,
    _FakeHTTP,
    _FakeResp,
    _no_rate_limit,
    _silence_connect_email,
    _user,
    redis_store,
)

pytestmark = pytest.mark.asyncio


async def test_linkedin_org_acl_fallback_and_unusable_names():
    http = _FakeHTTP({
        "https://api.linkedin.com/rest/organizationAcls": _FakeResp(500, {}),
        "https://api.linkedin.com/v2/organizationAcls": _FakeResp(200, {"elements": [
            {"organization": "urn:li:organization:777"},
            {"organization": "urn:li:organization:778"},
        ]}),
        "https://api.linkedin.com/rest/organizations/777": _FakeResp(200, {"name": {"localized": {}}}),
        "https://api.linkedin.com/rest/organizations/778": _FakeResp(200, {"localizedName": "   "}),
    })
    out = await auth._sync_linkedin_organizations(
        db=FakeDB(), team_id=uuid.uuid4(), access_token="at", refresh_token=None, scopes=[], http=http)
    assert sorted(a.display_name for a in out) == ["LinkedIn Page 777", "LinkedIn Page 778"]
    assert http.calls[0].startswith("https://api.linkedin.com/rest/organizationAcls")
    assert http.calls[1].startswith("https://api.linkedin.com/v2/organizationAcls")


async def test_refresh_legacy_token_without_jti_or_team(request):
    store = request.getfixturevalue("redis_store")
    u = _user()
    legacy = jwt.encode(
        {"sub": str(u.id), "type": "refresh", "exp": datetime.now(UTC) + timedelta(days=1)},
        auth.settings.JWT_SECRET_KEY, algorithm=auth.settings.JWT_ALGORITHM,
    )
    out = await auth.refresh_token(auth.RefreshRequest(refresh_token=legacy), FakeDB(users=[u], team_ids=[]))
    claims = decode_token(out.access_token)
    assert claims["sub"] == str(u.id) and "team_id" not in claims
    assert not any(k.startswith("auth:rt_") for k in store.store)  # nothing to burn


async def test_export_ignores_team_claim_without_membership(monkeypatch):
    u = _user()
    foreign = Team(id=uuid.uuid4(), name="other", owner_id=uuid.uuid4())
    tok = create_access_token({"sub": str(u.id), "team_id": str(foreign.id)})
    import app.api.deps as deps

    fallback = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "get_user_team", fallback)
    out = await auth.export_user_data(u, FakeDB(teams=[foreign]), tok)
    fallback.assert_awaited_once()
    assert out["posts"] == [] and out["accounts"] == []


async def test_threads_authorize_has_no_pkce(monkeypatch):
    captured = {}

    class FakeClient:
        client_id = "thid"
        client_secret = "thsec"

        async def get_authorization_url(self, redirect_uri, **kw):
            captured.update(kw)
            return "https://threads.net/oauth"

    monkeypatch.setattr(auth, "threads_client", FakeClient())
    monkeypatch.setattr(auth.settings, "THREADS_REDIRECT_URI", "https://x/cb")
    team_id = uuid.uuid4()
    await auth.oauth_authorize("threads", team_id, _user())
    assert captured["code_challenge"] is None and captured["code_challenge_method"] is None
    assert verify_oauth_state(captured["state"]) == {"t": str(team_id)}


_FB = "https://graph.facebook.com/v26.0"


class _FBClient:
    async def get_access_token(self, *a, **kw):
        return {"access_token": "short_at", "scope": "public_profile"}


def _fb_http(monkeypatch, **routes):
    base = {
        f"{_FB}/me/accounts": _FakeResp(200, {"data": []}),
        f"{_FB}/me/businesses": _FakeResp(200, {"data": []}),
        f"{_FB}/me": _FakeResp(200, {"id": "fb-1", "name": "Themis", "picture": {"data": {"url": "pic"}}}),
        f"{_FB}/oauth/access_token": _FakeResp(200, {"access_token": "ll_at", "expires_in": 5184000}),
    }
    merged = dict(routes)  # overrides first so their prefixes win
    for k, v in base.items():
        merged.setdefault(k, v)
    http = _FakeAsyncHTTPClient(merged)
    monkeypatch.setattr(auth, "facebook_client", _FBClient())
    monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
    return http


@pytest.mark.usefixtures("_silence_connect_email")
async def test_ig_onboarding_skips_page_backed_entry_without_id(monkeypatch):
    _fb_http(monkeypatch, **{
        f"{_FB}/me/accounts": _FakeResp(200, {"data": [{"id": "pg1", "access_token": "pt1"}]}),
        f"{_FB}/pg1/page_backed_instagram_accounts": _FakeResp(200, {"data": [
            {"username": "ghost"}, {"id": "ig-pb", "username": "cloudless.gr"}]}),
    })
    db = FakeDB(users=[_user()])
    state = sign_oauth_state({"t": str(uuid.uuid4()), "p": "instagram-onboarding"})
    await auth.oauth_callback("facebook", state=state, db=db, code="c")
    acct = next(a for a in db.added if isinstance(a, SocialAccount))
    assert acct.account_id == "ig-pb"


@pytest.mark.usefixtures("_silence_connect_email")
async def test_whatsapp_no_business_has_a_waba(monkeypatch):
    http = _fb_http(monkeypatch, **{
        f"{_FB}/me/businesses": _FakeResp(200, {"data": [{"id": "b1"}, {"id": "b2"}]}),
        f"{_FB}/b1/owned_whatsapp_business_accounts": _FakeResp(403, {}),
        f"{_FB}/b2/owned_whatsapp_business_accounts": _FakeResp(200, {"data": []}),
    })
    db = FakeDB(users=[_user()])
    await auth.oauth_callback("facebook", state=sign_oauth_state({"t": str(uuid.uuid4()), "p": "whatsapp"}), db=db, code="c")
    acct = next(a for a in db.added if isinstance(a, SocialAccount))
    assert acct.platform == "whatsapp"
    assert acct.meta_data["waba_id"] is None and acct.meta_data["phone_numbers"] == []
    assert not any("phone_numbers" in u for u in http.calls)


@pytest.mark.usefixtures("_silence_connect_email")
async def test_whatsapp_phone_lookup_failure_keeps_waba(monkeypatch):
    _fb_http(monkeypatch, **{
        f"{_FB}/me/businesses": _FakeResp(200, {"data": [{"id": "b1"}, {"id": "b2"}]}),
        f"{_FB}/b1/owned_whatsapp_business_accounts": _FakeResp(200, {"data": [{"id": "waba1"}]}),
        f"{_FB}/waba1/phone_numbers": _FakeResp(500, {}),
    })
    db = FakeDB(users=[_user()])
    await auth.oauth_callback("facebook", state=sign_oauth_state({"t": str(uuid.uuid4()), "p": "whatsapp"}), db=db, code="c")
    acct = next(a for a in db.added if isinstance(a, SocialAccount))
    assert acct.meta_data["waba_id"] == "waba1" and acct.meta_data["phone_numbers"] == []


async def test_messenger_alias_callback_is_unsupported(monkeypatch):
    _fb_http(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await auth.oauth_callback("messenger", state=sign_oauth_state({"t": str(uuid.uuid4())}), db=FakeDB(), code="c")
    assert ei.value.status_code == 400 and ei.value.detail == "Unsupported platform"


@pytest.mark.usefixtures("_silence_connect_email")
async def test_instagram_skips_id_less_entries_then_business_manager(monkeypatch):
    class FakeClient:
        async def get_access_token(self, *a, **kw):
            return {"access_token": "at"}

    http = _FakeAsyncHTTPClient({
        fbu("me/accounts"): _FakeResp(200, {"data": [{"id": "p1", "access_token": "pt"}]}),
        fbu("oauth/access_token"): _FakeResp(200, {"access_token": "ll"}),
        fbu("p1/page_backed_instagram_accounts"): _FakeResp(200, {"data": [{"username": "ghost"}]}),
        fbu("me/businesses"): _FakeResp(200, {"data": [{"id": "biz1"}]}),
        fbu("biz1/instagram_accounts"): _FakeResp(200, {"data": [{"username": "ghost2"}, {"id": "ig-bm", "username": "cl"}]}),
        fbu("me"): _FakeResp(200, {"id": "fb1", "name": "F"}),
    })
    monkeypatch.setattr(auth, "instagram_client", FakeClient())
    monkeypatch.setattr(auth.settings, "INSTAGRAM_REDIRECT_URI", "https://x/cb")
    monkeypatch.setattr(auth.settings, "FACEBOOK_CLIENT_ID", "cid")
    monkeypatch.setattr(auth.settings, "FACEBOOK_CLIENT_SECRET", "cs")
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
    db = FakeDB(users=[_user()])
    await auth.oauth_callback("instagram", state=sign_oauth_state({"t": str(uuid.uuid4())}), db=db, code="c")
    acct = next(a for a in db.added if isinstance(a, SocialAccount))
    assert acct.account_id == "ig-bm"


@pytest.mark.usefixtures("_silence_connect_email")
async def test_tiktok_regrant_keeps_scope_gated_meta(monkeypatch):
    team_id = uuid.uuid4()
    existing = SocialAccount(
        id=uuid.uuid4(), team_id=team_id, platform="tiktok", account_id="oid-1", username="cl",
        status="active", account_type="person", access_token_enc=encrypt_token("old"),
        meta_data={"follower_count": 5, "bio_description": "b"}, scopes=[],
    )

    class FakeClient:
        async def get_access_token(self, *a, **kw):
            return {"access_token": "tt_at", "open_id": "oid-1"}

    http = _FakeAsyncHTTPClient({"https://open.tiktokapis.com/v2/user/info/": _FakeResp(
        200, {"data": {"user": {"open_id": "oid-1", "display_name": "C"}}})})
    monkeypatch.setattr(auth, "tiktok_client", FakeClient())
    monkeypatch.setattr(auth.settings, "TIKTOK_REDIRECT_URI", "https://x/cb")
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
    await auth.oauth_callback("tiktok", state=sign_oauth_state({"t": str(team_id)}), db=FakeDB(users=[_user()], accounts=[existing]),
                              code="c", granted_scopes=["user.info.basic", "user.info.profile", "user.info.stats"])
    assert existing.meta_data["follower_count"] == 5 and existing.meta_data["bio_description"] == "b"


async def test_owner_opted_out_of_connect_email(monkeypatch):
    team_id = uuid.uuid4()
    owner = _user(id=team_id, notification_preferences={"email_account_connected": False})

    class FakeClient:
        async def get_access_token(self, *a, **kw):
            return {"access_token": "at"}

    http = _FakeAsyncHTTPClient({"https://api.x.com/2/users/me": _FakeResp(200, {"data": {"id": "1", "username": "x", "name": "X"}})})
    monkeypatch.setattr(auth, "twitter_client", FakeClient())
    monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
    import app.services.email_templates as et

    send = AsyncMock()
    monkeypatch.setattr(et, "send_account_connected_email", send)
    out = await auth.oauth_callback("twitter", state=sign_oauth_state({"t": str(team_id)}), db=FakeDB(users=[owner]), code="c")
    assert "connected successfully" in out["message"]
    send.assert_not_called()


@pytest.mark.usefixtures("_silence_connect_email")
async def test_ig2_long_lived_token_without_expiry(monkeypatch):
    existing = SocialAccount(team_id=uuid.uuid4(), platform="instagram", account_id="ig-42", username="old",
                             status="active", access_token_enc=encrypt_token("x"), meta_data={})
    http = TestInstagram2Callback()._ig2_http(ll_post=_FakeResp(200, {"access_token": "ll_ig"}))
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
    await auth.instagram2_callback(state=sign_oauth_state({"t": str(existing.team_id)}), db=FakeDB(accounts=[existing]), code="c")
    assert existing.token_expires_at is None
    assert "instagram_token_expires_at" not in existing.meta_data


async def test_data_deletion_without_page_id(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(auth.settings, "FACEBOOK_APP_SECRET", "appsecret")
    user_acct = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(), account_id="u1")
    page_acct = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(), account_id="pg1")
    del_db = FakeDB(accounts=[user_acct, page_acct])

    class _Factory:
        async def __aenter__(self):
            return del_db

        async def __aexit__(self, *a):
            return False

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "async_session_maker", lambda: _Factory())
    await auth.meta_data_deletion_callback(TestMetaDataDeletion()._req({"user_id": "u1"}))
    for _ in range(4):
        await asyncio.sleep(0)
    assert del_db.deleted == [user_acct] and del_db.commits == 1
