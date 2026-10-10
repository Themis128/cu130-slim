"""Unit tests for app.api.accounts — PKCE/state helpers, meta scrubbing,
connect flows, account test/validate matrix, token refresh, business sync.

Route functions are invoked directly with a fake AsyncSession; OAuth clients
and platform API clients are monkeypatched. No network, no database.
"""
import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api import accounts
from app.core.security import encrypt_token
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User, UserRole

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def scalars(self):
        v = self._v if isinstance(self._v, list) else ([self._v] if self._v else [])
        return _Scalars(v)


class FakeDB:
    def __init__(self, accounts=(), teams=(), memberships=(), users=()):
        self.accounts = list(accounts)
        self.teams = list(teams)
        self.memberships = list(memberships)
        self.users = list(users)
        self.added = []
        self.deleted = []
        self.commits = 0
        self.flushes = 0
        self._team_get = {t.id: t for t in self.teams}

    _PLATFORMS = {
        "linkedin", "twitter", "facebook", "instagram", "threads", "tiktok",
        "whatsapp", "messenger", "telegram", "viber", "bluesky",
    }

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        cparams = dict(stmt.compile().params)
        params = set(cparams.values())

        def _vals(prefix):
            return [v for k, v in cparams.items() if k == prefix or k.startswith(prefix + "_")]

        if entity is SocialAccount:
            res = list(self.accounts)
            ids = _vals("id")
            if ids:
                res = [a for a in res if a.id in ids]
            teams = _vals("team_id")
            if teams:
                res = [a for a in res if a.team_id in teams]
            ext = _vals("account_id")
            if ext:
                res = [a for a in res if a.account_id in ext]
            plats = self._PLATFORMS & {p for p in params if isinstance(p, str)}
            if plats:
                res = [a for a in res if a.platform in plats]
            if cparams.get("is_business") is True:
                res = [a for a in res if a.is_business]
            return _Result(res)
        if entity is Team:
            by_id = [t for t in self.teams if t.id in params]
            return _Result(by_id or list(self.teams))
        if entity is TeamMember:
            matched = [
                m for m in self.memberships
                if m.team_id in params or m.user_id in params or not params
            ]
            return _Result(matched)
        if entity is User:
            return _Result(list(self.users))
        return _Result(None)

    async def get(self, model, key):
        if model is Team:
            return self._team_get.get(key)
        return None

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, SocialAccount):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.created_at is None:
                obj.created_at = datetime.now(UTC)
            if obj.scopes is None:
                obj.scopes = []
            if obj.meta_data is None:
                obj.meta_data = {}
            self.accounts.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def delete(self, obj):
        self.deleted.append(obj)


def _user() -> User:
    return User(id=uuid.uuid4(), email="u@x.io", name="U")


def _team() -> Team:
    t = Team(id=uuid.uuid4(), name="t", owner_id=uuid.uuid4())
    t.plan_tier = "enterprise"
    return t


def _account(platform="twitter", **kw) -> SocialAccount:
    a = SocialAccount(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        platform=platform,
        account_id=kw.get("account_id", "ext-1"),
        username=kw.get("username", "acct"),
        display_name=kw.get("display_name", "Acct"),
        status=kw.get("status", "active"),
        is_business=kw.get("is_business", False),
        account_type=kw.get("account_type", "person"),
        access_token_enc=kw.get("access_token_enc", encrypt_token("tok")),
        refresh_token_enc=kw.get("refresh_token_enc"),
        token_expires_at=kw.get("token_expires_at"),
        scopes=kw.get("scopes", ["s1"]),
        meta_data=kw.get("meta_data", {}),
        created_at=kw.get("created_at", datetime.now(UTC)),
    )
    return a


class _FakeOAuthClient:
    def __init__(self, url="https://auth/x", token=None, error=None):
        self.url = url
        self.token = token
        self.error = error
        self.calls = []

    async def get_authorization_url(self, redirect_uri, **kw):
        self.calls.append(kw)
        return self.url

    async def refresh_token(self, refresh_token, *a, **kw):
        if self.error:
            raise self.error
        return self.token or {"access_token": "new_at"}


# ---------------------------------------------------------------------------
# Helpers + response scrubbing
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_pkce_pair_s256(self):
        verifier, challenge = accounts._pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected and len(verifier) > 40

    def test_encode_decode_state_roundtrip(self):
        team_id = uuid.uuid4()
        state = accounts._encode_state(team_id, "cv-1", platform="tiktok")
        decoded_team, cv = accounts._decode_state(state)
        assert decoded_team == team_id and cv == "cv-1"

    def test_decode_state_legacy_plain_uuid(self):
        team_id = uuid.uuid4()
        assert accounts._decode_state(str(team_id)) == (team_id, None)

    def test_decode_state_legacy_b64_json(self):
        team_id = uuid.uuid4()
        legacy = base64.urlsafe_b64encode(json.dumps({"t": str(team_id), "cv": "c"}).encode()).decode().rstrip("=")
        assert accounts._decode_state(legacy) == (team_id, "c")

    def test_strip_secret_meta_recursive(self):
        v = {
            "page_token": "sekrit",
            "safe": "ok",
            "nested": {"access_token": "x", "keep": 1},
            "list": [{"password": "p", "v": 2}],
            "browser_storage_state": {"cookies": []},
            "session_id": "s",
        }
        out = accounts.SocialAccountResponse(
            id=uuid.uuid4(), platform="facebook", account_id="1",
            username=None, display_name=None, avatar_url=None,
            status="active", scopes=[], token_expires_at=None,
            created_at="2026-01-01", meta_data=v,
        )
        meta = out.meta_data
        assert "page_token" not in meta and meta["safe"] == "ok"
        assert meta["nested"] == {"keep": 1}
        assert meta["list"] == [{"v": 2}]
        assert "browser_storage_state" not in meta and "session_id" not in meta

    def test_coerce_team_id_variants(self):
        assert accounts.ConnectBodyRequest(platform="x", team_id=None).team_id is None
        tid = uuid.uuid4()
        assert accounts.ConnectBodyRequest(platform="x", team_id=str(tid)).team_id == tid
        assert accounts.ConnectBodyRequest(platform="x", team_id="not-a-uuid").team_id is None


# ---------------------------------------------------------------------------
# list / get / disconnect
# ---------------------------------------------------------------------------

class TestAccountCRUD:
    pytestmark = pytest.mark.asyncio

    async def test_list_accounts_serializes(self):
        team = _team()
        a = _account(team_id=team.id, token_expires_at=datetime.now(UTC))
        db = FakeDB(accounts=[a])
        out = await accounts.list_accounts(team.id, db=db)
        assert len(out) == 1 and out[0].platform == "twitter"
        assert out[0].token_expires_at is not None

    async def test_list_accounts_filters(self):
        team = _team()
        a = _account(team_id=team.id)
        db = FakeDB(accounts=[a])
        out = await accounts.list_accounts(team.id, platform="twitter", is_business=False, db=db)
        assert len(out) == 1

    async def test_get_account_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.get_account(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_get_account_returns_response(self):
        a = _account()
        out = await accounts.get_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out.platform == "twitter" and out.account_type == "person"

    async def test_disconnect_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.disconnect_account(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_disconnect_deletes(self):
        a = _account()
        db = FakeDB(accounts=[a])
        await accounts.disconnect_account(a.id, _user(), db)
        assert a in db.deleted and db.commits == 1


# ---------------------------------------------------------------------------
# Connect flows
# ---------------------------------------------------------------------------

class TestConnect:
    pytestmark = pytest.mark.asyncio

    async def test_connect_body_no_team_403(self, monkeypatch):
        monkeypatch.setattr(accounts, "check_quota", lambda *a, **k: None)
        with pytest.raises(HTTPException) as e:
            await accounts.connect_account_body(
                accounts.ConnectBodyRequest(platform="twitter"), _user(), FakeDB(),
            )
        assert e.value.status_code == 403

    async def test_connect_body_unsupported_400(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        team = _team()
        db = FakeDB(teams=[team])
        with pytest.raises(HTTPException) as e:
            await accounts.connect_account_body(
                accounts.ConnectBodyRequest(platform="mastodon"), _user(), db,
            )
        assert e.value.status_code == 400

    async def test_connect_body_twitter_uses_pkce(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        client = _FakeOAuthClient()
        monkeypatch.setitem(accounts.PLATFORM_CLIENTS, "twitter", client)
        monkeypatch.setattr(accounts.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        team = _team()
        db = FakeDB(teams=[team])
        out = await accounts.connect_account_body(
            accounts.ConnectBodyRequest(platform="twitter"), _user(), db,
        )
        assert out.authorization_url == "https://auth/x"
        call = client.calls[0]
        assert call["code_challenge"] and call["code_challenge_method"] == "S256"
        assert "tweet.write" in call["scope"]

    async def test_connect_body_tiktok_client_key(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        client = _FakeOAuthClient()
        monkeypatch.setitem(accounts.PLATFORM_CLIENTS, "tiktok", client)
        monkeypatch.setattr(accounts.settings, "TIKTOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(accounts.settings, "TIKTOK_CLIENT_KEY", "ttk")
        team = _team()
        await accounts.connect_account_body(
            accounts.ConnectBodyRequest(platform="tiktok"), _user(), FakeDB(teams=[team]),
        )
        call = client.calls[0]
        assert call["extras_params"]["client_key"] == "ttk"
        assert call["scope"] is None  # comma-joined inside extras instead
        assert "," in call["extras_params"]["scope"]

    async def test_connect_body_whatsapp_uses_facebook_client(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        client = _FakeOAuthClient()
        monkeypatch.setitem(accounts.PLATFORM_CLIENTS, "whatsapp", client)
        monkeypatch.setattr(accounts.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        team = _team()
        await accounts.connect_account_body(
            accounts.ConnectBodyRequest(platform="whatsapp"), _user(), FakeDB(teams=[team]),
        )
        call = client.calls[0]
        assert "whatsapp_business_messaging" in call["scope"]

    async def test_connect_path_non_member_403(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        with pytest.raises(HTTPException) as e:
            await accounts.connect_account("twitter", uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 403

    async def test_connect_path_authorize(self, monkeypatch):
        async def quota(*a, **k):
            return None
        monkeypatch.setattr(accounts, "check_quota", quota)
        client = _FakeOAuthClient()
        monkeypatch.setitem(accounts.PLATFORM_CLIENTS, "threads", client)
        monkeypatch.setattr(accounts.settings, "THREADS_REDIRECT_URI", "https://x/cb")
        team_id = uuid.uuid4()
        m = TeamMember(team_id=team_id, user_id=uuid.uuid4(), role=UserRole.OWNER)
        u = _user()
        m.user_id = u.id
        out = await accounts.connect_account(
            "threads", team_id, u, FakeDB(memberships=[m]),
        )
        assert out.authorization_url == "https://auth/x"
        assert "threads_content_publish" in client.calls[0]["scope"]


# ---------------------------------------------------------------------------
# test_account + validate_account — per-platform matrix
# ---------------------------------------------------------------------------

class _FakePlatformClient:
    error = None
    created_with = None

    def __init__(self, **kw):
        type(self).created_with = kw

    async def validate_token(self):
        if type(self).error:
            raise type(self).error


class TestValidateMatrix:
    pytestmark = pytest.mark.asyncio

    async def test_test_account_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.test_account(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_test_account_twitter_valid(self, monkeypatch):
        _FakePlatformClient.error = None
        monkeypatch.setattr(accounts, "TwitterAPIClient", _FakePlatformClient)
        a = _account("twitter", status="expired")
        db = FakeDB(accounts=[a])
        out = await accounts.test_account(a.id, _user(), db)
        assert out["valid"] is True and a.status == "active" and db.commits == 1

    async def test_test_account_401_marks_expired(self, monkeypatch):
        from app.services.twitter_api import TwitterAPIError
        _FakePlatformClient.error = TwitterAPIError(401, "bad token", "https://api.x")
        monkeypatch.setattr(accounts, "TwitterAPIClient", _FakePlatformClient)
        a = _account("twitter", status="active")
        db = FakeDB(accounts=[a])
        out = await accounts.test_account(a.id, _user(), db)
        assert out["valid"] is False and a.status == "expired" and db.commits == 1
        _FakePlatformClient.error = None

    async def test_test_account_non_auth_error(self, monkeypatch):
        from app.services.twitter_api import TwitterAPIError
        _FakePlatformClient.error = TwitterAPIError(500, "server", "https://api.x")
        monkeypatch.setattr(accounts, "TwitterAPIClient", _FakePlatformClient)
        a = _account("twitter", status="active")
        out = await accounts.test_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out["tested"] is True and "HTTP 500" in out["message"]
        _FakePlatformClient.error = None

    async def test_test_account_unknown_platform(self):
        a = _account("telegram")
        out = await accounts.test_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out["tested"] is False

    async def test_test_account_network_error(self, monkeypatch):
        class Boom:
            def __init__(self, **kw):
                raise ConnectionError("dns")

        monkeypatch.setattr(accounts, "TwitterAPIClient", Boom)
        a = _account("twitter")
        out = await accounts.test_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out["tested"] is False and out["message"] == "Network error"

    async def test_validate_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.validate_account(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_validate_tiktok_valid(self, monkeypatch):
        _FakePlatformClient.error = None
        monkeypatch.setattr(accounts, "TikTokAPIClient", _FakePlatformClient)
        a = _account("tiktok")
        out = await accounts.validate_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out["valid"] is True and out["checked"] is True

    async def test_validate_expired_marks(self, monkeypatch):
        from app.services.tiktok_api import TikTokAPIError
        _FakePlatformClient.error = TikTokAPIError(403, "denied", "https://api.tt")
        monkeypatch.setattr(accounts, "TikTokAPIClient", _FakePlatformClient)
        a = _account("tiktok", status="active")
        db = FakeDB(accounts=[a])
        out = await accounts.validate_account(a.id, _user(), db)
        assert out["valid"] is False and a.status == "expired"
        _FakePlatformClient.error = None

    async def test_validate_unknown_platform_unchecked(self):
        a = _account("viber")
        out = await accounts.validate_account(a.id, _user(), FakeDB(accounts=[a]))
        assert out["checked"] is False


# ---------------------------------------------------------------------------
# refresh_account_token
# ---------------------------------------------------------------------------

class TestRefreshAccountToken:
    pytestmark = pytest.mark.asyncio

    async def test_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_no_refresh_token_400(self):
        a = _account("twitter", refresh_token_enc=None)
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(a.id, _user(), FakeDB(accounts=[a]))
        assert "No refresh token" in e.value.detail

    async def test_unsupported_platform_400(self):
        a = _account("whatsapp", refresh_token_enc=encrypt_token("rt"))
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_missing_redirect_uri_500(self, monkeypatch):
        a = _account("threads", refresh_token_enc=encrypt_token("rt"))
        monkeypatch.setattr(accounts.settings, "THREADS_REDIRECT_URI", None)
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 500

    async def test_success_updates_tokens_and_expiry(self, monkeypatch):
        client = _FakeOAuthClient(token={"access_token": "fresh", "refresh_token": "fresh_rt",
                                         "expires_in": 7200})
        monkeypatch.setattr(accounts, "twitter_client", client)
        monkeypatch.setattr(accounts.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        async def log(*a, **k):
            return None
        monkeypatch.setattr(accounts, "log_action", log)
        a = _account("twitter", refresh_token_enc=encrypt_token("old_rt"),
                     token_expires_at=datetime.now(UTC) - timedelta(days=1))
        db = FakeDB(accounts=[a])
        out = await accounts.refresh_account_token(a.id, _user(), db)
        assert out["status"] == "active"
        from app.core.security import decrypt_token
        assert decrypt_token(a.access_token_enc) == "fresh"
        assert decrypt_token(a.refresh_token_enc) == "fresh_rt"
        assert a.token_expires_at > datetime.now(UTC)
        assert db.commits == 1

    async def test_tiktok_default_24h_expiry(self, monkeypatch):
        client = _FakeOAuthClient(token={"access_token": "fresh"})  # no expires_in
        monkeypatch.setattr(accounts, "tiktok_client", client)
        monkeypatch.setattr(accounts.settings, "TIKTOK_REDIRECT_URI", "https://x/cb")
        async def log(*a, **k):
            return None
        monkeypatch.setattr(accounts, "log_action", log)
        a = _account("tiktok", refresh_token_enc=encrypt_token("rt"))
        db = FakeDB(accounts=[a])
        await accounts.refresh_account_token(a.id, _user(), db)
        delta = a.token_expires_at - datetime.now(UTC)
        assert 23 < delta.total_seconds() / 3600 < 25

    async def test_provider_failure_400(self, monkeypatch):
        client = _FakeOAuthClient(error=RuntimeError("provider said no"))
        monkeypatch.setattr(accounts, "threads_client", client)
        monkeypatch.setattr(accounts.settings, "THREADS_REDIRECT_URI", "https://x/cb")
        a = _account("threads", refresh_token_enc=encrypt_token("rt"))
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(a.id, _user(), FakeDB(accounts=[a]))
        assert "Token refresh failed" in e.value.detail

    async def test_empty_access_token_400(self, monkeypatch):
        client = _FakeOAuthClient(token={"access_token": ""})
        monkeypatch.setattr(accounts, "twitter_client", client)
        monkeypatch.setattr(accounts.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        a = _account("twitter", refresh_token_enc=encrypt_token("rt"))
        with pytest.raises(HTTPException) as e:
            await accounts.refresh_account_token(a.id, _user(), FakeDB(accounts=[a]))
        assert "No access_token" in e.value.detail


# ---------------------------------------------------------------------------
# LinkedIn org sync route + business sync
# ---------------------------------------------------------------------------

class TestSyncRoutes:
    pytestmark = pytest.mark.asyncio

    async def test_sync_orgs_no_team_403(self):
        with pytest.raises(HTTPException) as e:
            await accounts.sync_linkedin_organizations(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 403

    async def test_sync_orgs_no_linkedin_account_400(self):
        team = _team()
        with pytest.raises(HTTPException) as e:
            await accounts.sync_linkedin_organizations(
                team.id, _user(), FakeDB(teams=[team]),
            )
        assert "personal account" in e.value.detail

    async def test_sync_orgs_happy(self, monkeypatch):
        team = _team()
        person = _account("linkedin", team_id=team.id,
                          meta_data={"account_type": "person"})

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        async def fake_sync(**kw):
            org = _account("linkedin", team_id=team.id,
                           account_id="777", display_name="Org",
                           account_type="organization", is_business=True,
                           meta_data={"account_type": "organization"})
            return [org]

        import app.api.auth as auth_mod
        monkeypatch.setattr(auth_mod, "_sync_linkedin_organizations", fake_sync)
        monkeypatch.setattr(accounts.httpx, "AsyncClient", lambda *a, **kw: FakeHTTP())
        db = FakeDB(accounts=[person], teams=[team])
        out = await accounts.sync_linkedin_organizations(team.id, _user(), db)
        assert out["synced"][0]["account_id"] == "777" and out["hint"] is None
        assert db.commits == 1

    async def test_sync_business_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.sync_business_accounts(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_sync_business_facebook_pages(self, monkeypatch):
        team = _team()
        personal = _account("facebook", team_id=team.id)
        page = _account("facebook", team_id=team.id, account_id="pg-1",
                        is_business=True, account_type="page")

        class FBClient:
            def __init__(self, **kw):
                pass

            async def get_pages(self):
                return [{"id": "pg-1", "name": "Cloudless", "access_token": "ptok",
                         "category": "App"}]

        monkeypatch.setattr(accounts, "FacebookAPIClient", FBClient)
        db = FakeDB(accounts=[personal, page])
        out = await accounts.sync_business_accounts(personal.id, _user(), db)
        assert out.platform == "facebook"
        assert page.display_name == "Cloudless" and page.is_business is True

    async def test_sync_business_instagram_discovers_ig_accounts(self, monkeypatch):
        team = _team()
        personal = _account("instagram", team_id=team.id)

        class FBClient:
            def __init__(self, **kw):
                pass

            async def get_pages(self):
                return [{"id": "pg-1", "name": "P",
                         "instagram_business_account": {"id": "ig-77"}}]

        monkeypatch.setattr(accounts, "FacebookAPIClient", FBClient)
        db = FakeDB(accounts=[personal])
        out = await accounts.sync_business_accounts(personal.id, _user(), db)
        assert out.platform == "instagram"
        new_acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert new_acct.account_id == "ig-77" and new_acct.is_business is True

    async def test_sync_business_unsupported_platform_400(self):
        a = _account("tiktok")
        with pytest.raises(HTTPException) as e:
            await accounts.sync_business_accounts(a.id, _user(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_set_business_account_404_parent(self):
        with pytest.raises(HTTPException) as e:
            await accounts.set_business_account(uuid.uuid4(), "biz-1", _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_set_business_account_404_business(self):
        parent = _account("facebook")
        with pytest.raises(HTTPException) as e:
            await accounts.set_business_account(
                parent.id, "missing-biz", _user(), FakeDB(accounts=[parent]),
            )
        assert "Business account" in e.value.detail

    async def test_set_business_account_success(self):
        team = _team()
        parent = _account("facebook", team_id=team.id)
        biz = _account("facebook", team_id=team.id, account_id="biz-9",
                       is_business=True, account_type="page")
        out = await accounts.set_business_account(
            parent.id, "biz-9", _user(), FakeDB(accounts=[parent, biz]),
        )
        assert out["business_account_id"] == "biz-9"
        assert out["account_id"] == str(biz.id)


# ---------------------------------------------------------------------------
# Facebook Page profile endpoints
# ---------------------------------------------------------------------------

class _FakeFBPageClient:
    """Fake FacebookAPIClient for Page-profile endpoints."""
    page_info = {"about": "A", "website": "https://x"}
    tasks_result = ["MANAGE"]
    error: Exception | None = None
    update_result = True
    upload_result = True
    cover_result = "photo-1"
    assigned_users = [{"id": "bu-1"}]
    assign_result = True
    calls: list = []

    def __init__(self, **kw):
        type(self).calls = []

    async def get_page_info(self):
        if self.error:
            raise self.error
        return self.page_info

    async def get_page_tasks(self):
        if self.error:
            raise self.error
        return self.tasks_result

    async def update_page_info(self, **kw):
        type(self).calls.append(kw)
        if self.error:
            raise self.error
        return self.update_result

    async def upload_profile_picture(self, data):
        type(self).calls.append(data)
        if self.error:
            raise self.error
        return self.upload_result

    async def upload_cover_photo(self, data):
        if self.error:
            raise self.error
        return self.cover_result

    async def get_assigned_users(self, business_id):
        if self.error:
            raise self.error
        return self.assigned_users

    async def assign_page_tasks(self, **kw):
        if self.error:
            raise self.error
        return self.assign_result


class _Upload:
    def __init__(self, data=b"img"):
        self._d = data

    async def read(self):
        return self._d


class TestFacebookPageProfile:
    pytestmark = pytest.mark.asyncio

    def _page(self, team=None):
        return _account("facebook", team_id=(team or _team()).id,
                        account_id="pg-1", is_business=True, account_type="page")

    async def test_page_profile_404(self):
        with pytest.raises(HTTPException) as e:
            await accounts.get_facebook_page_profile(uuid.uuid4(), _user(), FakeDB())
        assert e.value.status_code == 404

    async def test_page_profile_requires_page_400(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        user_acct = _account("facebook", is_business=False)
        with pytest.raises(HTTPException) as e:
            await accounts.get_facebook_page_profile(
                user_acct.id, _user(), FakeDB(accounts=[user_acct]),
            )
        assert e.value.status_code == 400 and "Page account" in e.value.detail

    async def test_page_profile_returns_info_and_tasks(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        out = await accounts.get_facebook_page_profile(
            page.id, _user(), FakeDB(accounts=[page]),
        )
        assert out["profile"] == _FakeFBPageClient.page_info
        assert out["tasks"] == ["MANAGE"]

    async def test_page_profile_info_error_maps_status(self, monkeypatch):
        from app.services.facebook_api import FacebookAPIError
        _FakeFBPageClient.error = FacebookAPIError(400, "bad", "https://g")
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        with pytest.raises(HTTPException) as e:
            await accounts.get_facebook_page_profile(
                page.id, _user(), FakeDB(accounts=[page]),
            )
        assert e.value.status_code == 400
        _FakeFBPageClient.error = None

    async def test_update_page_profile_success(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        out = await accounts.update_facebook_page_profile(
            page.id, accounts.PageProfileUpdate(about="new", website="https://y"),
            _user(), FakeDB(accounts=[page]),
        )
        assert out["success"] is True
        assert _FakeFBPageClient.calls[0]["about"] == "new"

    async def test_update_page_profile_value_error_400(self, monkeypatch):
        _FakeFBPageClient.error = ValueError("no fields")
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        with pytest.raises(HTTPException) as e:
            await accounts.update_facebook_page_profile(
                page.id, accounts.PageProfileUpdate(), _user(), FakeDB(accounts=[page]),
            )
        assert e.value.status_code == 400
        _FakeFBPageClient.error = None

    async def test_upload_picture_empty_400(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        with pytest.raises(HTTPException) as e:
            await accounts.upload_facebook_profile_picture(
                page.id, _user(), FakeDB(accounts=[page]), file=_Upload(b""),
            )
        assert "No image" in e.value.detail

    async def test_upload_picture_success(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        out = await accounts.upload_facebook_profile_picture(
            page.id, _user(), FakeDB(accounts=[page]), file=_Upload(b"png"),
        )
        assert out["success"] is True

    async def test_upload_cover_success(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        out = await accounts.upload_facebook_cover_photo(
            page.id, _user(), FakeDB(accounts=[page]), file=_Upload(b"jpg"),
        )
        assert out == {"success": True, "photo_id": "photo-1"}

    async def test_assign_manage_task_explicit_user(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        req = accounts.AssignManageTaskRequest(business_id="biz-1", business_user_id="bu-x")
        out = await accounts.assign_facebook_manage_task(
            page.id, req, _user(), FakeDB(accounts=[page]),
        )
        assert out["business_user_id"] == "bu-x" and "MANAGE" in out["tasks"]

    async def test_assign_manage_task_lookup_user(self, monkeypatch):
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        req = accounts.AssignManageTaskRequest(business_id="biz-1")
        out = await accounts.assign_facebook_manage_task(
            page.id, req, _user(), FakeDB(accounts=[page]),
        )
        assert out["business_user_id"] == "bu-1"  # from get_assigned_users

    async def test_assign_manage_task_no_user_400(self, monkeypatch):
        _FakeFBPageClient.assigned_users = []
        monkeypatch.setattr(accounts, "FacebookAPIClient", _FakeFBPageClient)
        page = self._page()
        req = accounts.AssignManageTaskRequest(business_id="biz-1")
        with pytest.raises(HTTPException) as e:
            await accounts.assign_facebook_manage_task(
                page.id, req, _user(), FakeDB(accounts=[page]),
            )
        assert e.value.status_code == 400
        _FakeFBPageClient.assigned_users = [{"id": "bu-1"}]
