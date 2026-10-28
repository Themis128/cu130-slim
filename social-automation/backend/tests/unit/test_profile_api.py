"""Unit tests for app.api.profile — dispatch, helpers, platform login,
Threads settings, and the Instagram/Threads profile chains.

Route functions and platform helpers are invoked directly with fakes;
service clients are monkeypatched. No network, no database.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api import profile
from app.core.security import decrypt_field
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.browser_bridge import BrowserBridgeError
from app.services.facebook_sidecar import FacebookSidecarError
from app.services.instagram_private_api import InstagramPrivateAPIError
from app.services.linkedin_sidecar import LinkedInSidecarError

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v


class FakeDB:
    def __init__(self, accounts=()):
        self.accounts = list(accounts)
        self.commits = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        params = set(stmt.compile().params.values())
        if entity is SocialAccount:
            for a in self.accounts:
                if a.id in params or a.account_id in params or not params:
                    return _Result(a)
            return _Result(None)
        return _Result(None)

    async def commit(self):
        self.commits += 1


class FakeUploadFile:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


def _user() -> User:
    u = User(id=uuid.uuid4(), email="u@x.io", name="U")
    return u


def _account(platform="instagram", **kw) -> SocialAccount:
    a = SocialAccount(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        platform=platform,
        account_id=kw.get("account_id", "ext-1"),
        username=kw.get("username", "cloudless.gr"),
        display_name=kw.get("display_name", "Cloudless"),
        is_business=kw.get("is_business", False),
        status="active",
        access_token_enc=kw.get("access_token_enc"),
        meta_data=kw.get("meta_data", {}),
    )
    return a


class FakeSecrets:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get(self, key):
        return self.values.get(key)


@pytest.fixture
def secrets(monkeypatch):
    store = FakeSecrets()
    monkeypatch.setattr(profile, "secret_store", store)
    return store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    @pytest.mark.asyncio
    async def test_get_account_found(self):
        a = _account()
        assert await profile._get_account(a.id, _user(), FakeDB(accounts=[a])) is a

    @pytest.mark.asyncio
    async def test_get_account_missing_404(self):
        with pytest.raises(HTTPException) as e:
            await profile._get_account(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    def test_get_meta_dict_passthrough(self):
        a = _account(meta_data={"k": 1})
        assert profile._get_meta(a) == {"k": 1}

    def test_get_meta_parses_json_string(self):
        a = _account(meta_data='{"k": 2}')
        assert profile._get_meta(a) == {"k": 2}

    def test_get_meta_bad_json_returns_empty(self):
        a = _account(meta_data="{broken")
        assert profile._get_meta(a) == {}

    def test_get_meta_none_returns_empty(self):
        a = _account(meta_data=None)
        assert profile._get_meta(a) == {}

    def test_session_id_roundtrip(self):
        a = _account(meta_data={})
        profile._set_instagram_session_id(a, "sess-123")
        enc = profile._get_meta(a)["private_api_session_id"]
        assert enc != "sess-123"  # stored encrypted
        assert decrypt_field(enc) == "sess-123"
        assert profile._get_instagram_session_id(a) == "sess-123"

    def test_session_id_absent(self):
        assert profile._get_instagram_session_id(_account(meta_data={})) is None


# ---------------------------------------------------------------------------
# Dispatch — every platform branch + error paths
# ---------------------------------------------------------------------------

class TestDispatch:
    pytestmark = pytest.mark.asyncio

    async def test_get_profile_dispatches(self, monkeypatch):
        sentinels = {}
        for name in ("_get_instagram_profile", "_get_facebook_page_profile",
                     "_get_facebook_user_profile", "_get_linkedin_profile",
                     "_get_twitter_profile", "_get_tiktok_profile", "_get_threads_profile"):
            async def fake(account, _n=name):
                return f"called:{_n}"
            monkeypatch.setattr(profile, name, fake)
            sentinels[name] = f"called:{name}"

        cases = [
            ("instagram", False, "_get_instagram_profile"),
            ("facebook", True, "_get_facebook_page_profile"),
            ("facebook", False, "_get_facebook_user_profile"),
            ("linkedin", False, "_get_linkedin_profile"),
            ("twitter", False, "_get_twitter_profile"),
            ("tiktok", False, "_get_tiktok_profile"),
            ("threads", False, "_get_threads_profile"),
        ]
        for platform_name, is_biz, fn in cases:
            a = _account(platform=platform_name, is_business=is_biz)
            out = await profile.get_profile(a.id, _user(), FakeDB(accounts=[a]))
            assert out == sentinels[fn]

    async def test_get_profile_unsupported_400(self):
        a = _account(platform="telegram")
        with pytest.raises(HTTPException) as e:
            await profile.get_profile(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_update_profile_dispatches(self, monkeypatch):
        for name in ("_update_instagram_profile", "_update_facebook_page_profile",
                     "_update_facebook_user_profile", "_update_linkedin_profile",
                     "_update_twitter_profile", "_update_tiktok_profile",
                     "_update_threads_profile"):
            async def fake(*a, _n=name):
                return f"called:{_n}"
            monkeypatch.setattr(profile, name, fake)

        req = profile.ProfileUpdateRequest(biography="bio")
        for platform_name, is_biz, fn in [
            ("instagram", False, "_update_instagram_profile"),
            ("facebook", True, "_update_facebook_page_profile"),
            ("facebook", False, "_update_facebook_user_profile"),
            ("linkedin", False, "_update_linkedin_profile"),
            ("twitter", False, "_update_twitter_profile"),
            ("tiktok", False, "_update_tiktok_profile"),
            ("threads", False, "_update_threads_profile"),
        ]:
            a = _account(platform=platform_name, is_business=is_biz)
            out = await profile.update_profile(a.id, req, _user(), FakeDB(accounts=[a]))
            assert out == f"called:{fn}"

    async def test_update_profile_unsupported_400(self):
        a = _account(platform="whatsapp")
        with pytest.raises(HTTPException) as e:
            await profile.update_profile(
                a.id, profile.ProfileUpdateRequest(), _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400

    async def test_picture_empty_file_400(self):
        a = _account()
        with pytest.raises(HTTPException) as e:
            await profile.upload_profile_picture(
                a.id, _user(), FakeDB(accounts=[a]), file=FakeUploadFile(b""),
            )
        assert e.value.status_code == 400

    async def test_picture_dispatches(self, monkeypatch):
        for name in ("_upload_instagram_picture", "_upload_facebook_page_picture",
                     "_upload_facebook_user_picture", "_upload_linkedin_picture",
                     "_upload_twitter_picture", "_upload_tiktok_picture"):
            async def fake(*a, _n=name):
                return f"called:{_n}"
            monkeypatch.setattr(profile, name, fake)
        for platform_name, is_biz, fn in [
            ("instagram", False, "_upload_instagram_picture"),
            ("facebook", True, "_upload_facebook_page_picture"),
            ("facebook", False, "_upload_facebook_user_picture"),
            ("linkedin", False, "_upload_linkedin_picture"),
            ("twitter", False, "_upload_twitter_picture"),
            ("tiktok", False, "_upload_tiktok_picture"),
        ]:
            a = _account(platform=platform_name, is_business=is_biz)
            out = await profile.upload_profile_picture(
                a.id, _user(), FakeDB(accounts=[a]), file=FakeUploadFile(b"img"),
            )
            assert out == f"called:{fn}"

    async def test_cover_empty_file_400(self):
        a = _account()
        with pytest.raises(HTTPException) as e:
            await profile.upload_cover_photo(
                a.id, _user(), FakeDB(accounts=[a]), file=FakeUploadFile(b""),
            )
        assert e.value.status_code == 400

    async def test_cover_dispatches(self, monkeypatch):
        for name in ("_upload_facebook_page_cover", "_upload_facebook_user_cover",
                     "_upload_linkedin_cover", "_upload_twitter_banner"):
            async def fake(*a, _n=name):
                return f"called:{_n}"
            monkeypatch.setattr(profile, name, fake)
        for platform_name, is_biz, fn in [
            ("facebook", True, "_upload_facebook_page_cover"),
            ("facebook", False, "_upload_facebook_user_cover"),
            ("linkedin", False, "_upload_linkedin_cover"),
            ("twitter", False, "_upload_twitter_banner"),
        ]:
            a = _account(platform=platform_name, is_business=is_biz)
            out = await profile.upload_cover_photo(
                a.id, _user(), FakeDB(accounts=[a]), file=FakeUploadFile(b"img"),
            )
            assert out == f"called:{fn}"

    async def test_cover_unsupported_400(self):
        a = _account(platform="instagram")
        with pytest.raises(HTTPException) as e:
            await profile.upload_cover_photo(
                a.id, _user(), FakeDB(accounts=[a]), file=FakeUploadFile(b"img"),
            )
        assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# platform_login
# ---------------------------------------------------------------------------

class _FakeIGClient:
    def __init__(self, result=None, error=None, settings=None):
        self.result = result or {}
        self.error = error
        self._settings = settings or {"ua": "x"}
        self.calls = []

    async def login(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result

    async def get_settings(self, session_id):
        return self._settings


class TestPlatformLogin:
    pytestmark = pytest.mark.asyncio

    async def test_instagram_missing_credentials_401(self, secrets):
        a = _account("instagram")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.InstagramLoginRequest(), _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 401

    async def test_instagram_success_stores_session(self, monkeypatch, secrets):
        a = _account("instagram", meta_data={})
        client = _FakeIGClient(result={"session_id": "sess-9"}, settings={"ua": "x"})
        monkeypatch.setattr(profile, "_get_instagram_private_client", lambda: client)
        db = FakeDB(accounts=[a])
        out = await profile.platform_login(
            a.id,
            profile.InstagramLoginRequest(username="u", password="p"),
            _user(), db,
        )
        assert out.logged_in is True
        meta = profile._get_meta(a)
        assert meta["private_api_settings"] == {"ua": "x"}
        assert profile._get_instagram_session_id(a) == "sess-9"
        assert db.commits == 1

    async def test_instagram_2fa_not_logged_in(self, monkeypatch, secrets):
        a = _account("instagram", meta_data={})
        client = _FakeIGClient(result={"session_id": "s", "two_factor_required": True,
                                       "message": "code sent"})
        monkeypatch.setattr(profile, "_get_instagram_private_client", lambda: client)
        out = await profile.platform_login(
            a.id, profile.InstagramLoginRequest(username="u", password="p"),
            _user(), FakeDB(accounts=[a]),
        )
        assert out.logged_in is False and out.two_factor_required is True
        assert profile._get_instagram_session_id(a) is None

    async def test_instagram_error_maps_status(self, monkeypatch, secrets):
        secrets.values["INSTAGRAM_USERNAME"] = "u"
        secrets.values["INSTAGRAM_PASSWORD"] = "p"
        client = _FakeIGClient(error=InstagramPrivateAPIError(429, "rate limited"))
        monkeypatch.setattr(profile, "_get_instagram_private_client", lambda: client)
        a = _account("instagram")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.InstagramLoginRequest(), _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 429

    async def test_facebook_missing_credentials_401(self, secrets):
        a = _account("facebook")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.BrowserLoginRequest(), _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 401

    async def test_facebook_success_stores_storage_state(self, monkeypatch, secrets):
        class FakeFB:
            async def login(self, u, p, verification_code=None):
                return {"logged_in": True, "storage_state": {"cookies": []}}

        monkeypatch.setattr(profile, "FacebookSidecarClient", lambda *a, **kw: FakeFB())
        a = _account("facebook", meta_data={})
        db = FakeDB(accounts=[a])
        out = await profile.platform_login(
            a.id, profile.BrowserLoginRequest(username="u", password="p"),
            _user(), db,
        )
        assert out.logged_in is True
        assert profile._get_meta(a)["browser_storage_state"] == {"cookies": []}
        assert db.commits == 1

    async def test_facebook_sidecar_error_maps(self, monkeypatch, secrets):
        class FakeFB:
            async def login(self, *a, **kw):
                raise FacebookSidecarError(503, "sidecar down")

        monkeypatch.setattr(profile, "FacebookSidecarClient", lambda *a, **kw: FakeFB())
        a = _account("facebook")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.BrowserLoginRequest(username="u", password="p"),
                _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 503

    async def test_linkedin_success(self, monkeypatch, secrets):
        class FakeLI:
            async def login(self, u, p, verification_code=None):
                return {"logged_in": True, "storage_state": {"s": 1}}

        monkeypatch.setattr(profile, "LinkedInSidecarClient", lambda *a, **kw: FakeLI())
        a = _account("linkedin", meta_data={})
        out = await profile.platform_login(
            a.id, profile.BrowserLoginRequest(username="u", password="p"),
            _user(), FakeDB(accounts=[a]),
        )
        assert out.logged_in is True
        assert profile._get_meta(a)["browser_storage_state"] == {"s": 1}

    async def test_linkedin_error_maps(self, monkeypatch, secrets):
        class FakeLI:
            async def login(self, *a, **kw):
                raise LinkedInSidecarError(500, "boom")

        monkeypatch.setattr(profile, "LinkedInSidecarClient", lambda *a, **kw: FakeLI())
        a = _account("linkedin")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.BrowserLoginRequest(username="u", password="p"),
                _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 500

    async def test_unsupported_platform_400(self, secrets):
        a = _account("tiktok")
        with pytest.raises(HTTPException) as e:
            await profile.platform_login(
                a.id, profile.BrowserLoginRequest(username="u", password="p"),
                _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# Threads settings endpoints
# ---------------------------------------------------------------------------

class _FakeBridge:
    def __init__(self, get_result=None, update_result=None, error=None):
        self.get_result = get_result
        self.update_result = update_result or {}
        self.error = error
        self.update_calls = []

    async def get_threads_settings(self, username):
        if self.error:
            raise self.error
        return self.get_result

    async def update_threads_settings(self, username, **kw):
        self.update_calls.append(kw)
        if self.error:
            raise self.error
        return self.update_result

    async def get_instagram_profile(self):
        if self.error:
            raise self.error
        return self.get_result

    async def get_threads_profile(self, username):
        if self.error:
            raise self.error
        return self.get_result

    async def update_threads_profile(self, username, **kw):
        self.update_calls.append(kw)
        if self.error:
            raise self.error
        return self.update_result


class TestThreadsSettings:
    pytestmark = pytest.mark.asyncio

    async def test_wrong_platform_400(self):
        a = _account("instagram")
        with pytest.raises(HTTPException) as e:
            await profile.get_threads_settings(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_no_username_400(self):
        a = _account("threads", username=None)
        with pytest.raises(HTTPException) as e:
            await profile.get_threads_settings(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_get_settings_success(self, monkeypatch):
        bridge = _FakeBridge(get_result={"show_instagram_badge": True, "show_recent_views": False})
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        out = await profile.get_threads_settings(a.id, _user(), FakeDB(accounts=[a]))
        assert out.show_instagram_badge is True and out.show_recent_views is False

    async def test_get_settings_bridge_error_503(self, monkeypatch):
        bridge = _FakeBridge(error=BrowserBridgeError(500, "down"))
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        with pytest.raises(HTTPException) as e:
            await profile.get_threads_settings(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 503

    async def test_update_settings_success(self, monkeypatch):
        bridge = _FakeBridge(update_result={"status": "updated", "updated_fields": ["badge"]})
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        out = await profile.update_threads_settings(
            a.id, profile.ThreadsSettingsUpdateRequest(show_instagram_badge=True),
            _user(), FakeDB(accounts=[a]),
        )
        assert out.success is True and out.updated_fields == ["badge"]
        assert bridge.update_calls[0]["show_instagram_badge"] is True

    async def test_update_settings_error_503(self, monkeypatch):
        bridge = _FakeBridge(error=BrowserBridgeError(500, "down"))
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        with pytest.raises(HTTPException) as e:
            await profile.update_threads_settings(
                a.id, profile.ThreadsSettingsUpdateRequest(), _user(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 503


# ---------------------------------------------------------------------------
# Platform impls: Instagram + Threads chains
# ---------------------------------------------------------------------------

class TestInstagramProfile:
    pytestmark = pytest.mark.asyncio

    async def test_sidecar_success(self, monkeypatch):
        class Client:
            async def get_account(self, session_id):
                return {"username": "ig", "full_name": "IG", "biography": "b"}

        monkeypatch.setattr(profile, "_get_instagram_private_client", lambda: Client())
        a = _account("instagram")
        profile._set_instagram_session_id(a, "sess")
        out = await profile._get_instagram_profile(a)
        assert out.username == "ig" and out.platform == "instagram"

    async def test_falls_back_to_bridge(self, monkeypatch):
        class Client:
            async def get_account(self, s):
                raise InstagramPrivateAPIError(401, "dead session")

        bridge = _FakeBridge(get_result={"username": "ig2"})
        monkeypatch.setattr(profile, "_get_instagram_private_client", lambda: Client())
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("instagram")
        profile._set_instagram_session_id(a, "sess")
        out = await profile._get_instagram_profile(a)
        assert out.username == "ig2"

    async def test_all_fail_503(self, monkeypatch):
        bridge = _FakeBridge(error=BrowserBridgeError(400, "no session"))
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)

        class FreeClient:
            async def get_user_by_username(self, u):
                from app.services.free_instagram_client import FreeInstagramError
                raise FreeInstagramError("nope")

        monkeypatch.setattr(profile, "free_instagram_client", FreeClient())
        a = _account("instagram")  # no session_id → skips sidecar
        with pytest.raises(HTTPException) as e:
            await profile._get_instagram_profile(a)
        assert e.value.status_code == 503


class TestThreadsProfile:
    pytestmark = pytest.mark.asyncio

    async def test_api_success(self, monkeypatch):
        from app.core.security import encrypt_token

        class API:
            def __init__(self, **kw):
                pass

            async def get_profile(self):
                return {"username": "th", "name": "TH", "threads_biography": "bio"}

        import app.services.threads_api as ta
        monkeypatch.setattr(ta, "ThreadsAPIClient", API)
        a = _account("threads", access_token_enc=encrypt_token("tok"))
        out = await profile._get_threads_profile(a)
        assert out.username == "th" and out.biography == "bio"

    async def test_bridge_fallback(self, monkeypatch):
        bridge = _FakeBridge(get_result={"username": "th", "biography": "b"})
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads", access_token_enc=None)  # no token → bridge path
        out = await profile._get_threads_profile(a)
        assert out.username == "th"

    async def test_no_username_400(self):
        a = _account("threads", username=None, access_token_enc=None)
        with pytest.raises(HTTPException) as e:
            await profile._get_threads_profile(a)
        assert e.value.status_code == 400

    async def test_bridge_error_503(self, monkeypatch):
        bridge = _FakeBridge(error=BrowserBridgeError(500, "down"))
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads", access_token_enc=None)
        with pytest.raises(HTTPException) as e:
            await profile._get_threads_profile(a)
        assert e.value.status_code == 503

    async def test_update_marks_unsupported_fields_ignored(self, monkeypatch):
        bridge = _FakeBridge(update_result={"status": "updated", "updated_fields": ["bio"],
                                            "ignored_fields": []})
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        out = await profile._update_threads_profile(
            a, profile.ProfileUpdateRequest(biography="b", website="https://x"),
        )
        assert out.success is True
        assert "website" in out.ignored_fields  # Threads API can't set website

    async def test_update_error_503(self, monkeypatch):
        bridge = _FakeBridge(error=BrowserBridgeError(500, "down"))
        monkeypatch.setattr(profile, "_get_browser_bridge_client", lambda p: bridge)
        a = _account("threads")
        with pytest.raises(HTTPException) as e:
            await profile._update_threads_profile(a, profile.ProfileUpdateRequest())
        assert e.value.status_code == 503
