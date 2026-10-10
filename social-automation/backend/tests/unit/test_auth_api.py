"""Unit tests for app.api.auth — login lockout, refresh rotation, 2FA,
password reset, OAuth authorize/callback dispatch, and LinkedIn org sync.

Route functions are invoked directly with a fake AsyncSession; Redis and
external HTTP are faked/patched. No network, no database.
"""

import base64
import hashlib
import hmac
import json
import struct
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from starlette.requests import Request as StarletteRequest

from app.api import auth
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_token,
    hash_password,
    sign_oauth_state,
)
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User, UserRole

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, store=None, fail=False):
        self.store = dict(store or {})
        self.ttls = {}
        self.fail = fail
        self.sets = []

    def _boom(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key):
        self._boom()
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self._boom()
        self.store[key] = value
        self.sets.append((key, value, ex))

    async def incr(self, key):
        self._boom()
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key, ttl):
        self._boom()
        self.ttls[key] = ttl
        return True

    async def ttl(self, key):
        self._boom()
        return self.ttls.get(key, 120)

    async def delete(self, key):
        self._boom()
        self.store.pop(key, None)


class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)


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
    def __init__(self, users=(), memberships=(), teams=(), team_ids=(), accounts=(), posts=(), media=(), snapshots=()):
        self.users = list(users)
        self.memberships = list(memberships)
        self.teams = list(teams)
        self.team_ids = list(team_ids)
        self.accounts = list(accounts)
        self.posts = list(posts)
        self.media = list(media)
        self.snapshots = list(snapshots)
        self.added = []
        self.deleted = []
        self.commits = 0
        self.flushes = 0
        self._team_get = {t.id: t for t in self.teams}

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        col = stmt.column_descriptions[0].get("name")
        params = set(stmt.compile().params.values())
        if entity is User:
            for u in self.users:
                if u.id in params or (u.email and u.email in params):
                    return _Result(u)
            return _Result(None)
        if entity is TeamMember:
            matched = [m for m in self.memberships if m.team_id in params or m.user_id in params or not params]
            # login/refresh team resolution selects Team.id — scalars().first()
            if col == "id":
                return _Result(self.team_ids)
            return _Result(matched)
        if entity is Team:
            if col == "id":
                return _Result(self.team_ids)
            matched = [t for t in self.teams if t.owner_id in params or t.id in params or not params]
            return _Result(matched)
        if entity is SocialAccount:
            matched = [a for a in self.accounts if a.team_id in params or a.account_id in params or not params]
            return _Result(matched)
        if entity is not None and getattr(entity, "__name__", "") == "Post":
            return _Result(self.posts)
        if entity is not None and getattr(entity, "__name__", "") == "MediaAsset":
            return _Result(self.media)
        if entity is not None and getattr(entity, "__name__", "") == "PostAnalyticsSnapshot":
            return _Result(self.snapshots)
        return _Result(None)

    async def get(self, model, key):
        if model is Team:
            return self._team_get.get(key)
        for u in self.users:
            if u.id == key:
                return u
        return None

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, User) and obj not in self.users:
            self.users.append(obj)
        if isinstance(obj, Team):
            self.teams.append(obj)
            self._team_get[obj.id] = obj
        if isinstance(obj, TeamMember):
            self.memberships.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        self.deleted.append(obj)


def _request(path="/x") -> Request:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


def _user(**kw) -> User:
    u = User(
        id=kw.get("id", uuid.uuid4()),
        email=kw.get("email", "u@x.io"),
        password_hash=kw.get("password_hash", hash_password("pw123456")),
        name=kw.get("name", "U"),
        timezone=kw.get("timezone", "UTC"),
    )
    u.two_factor_enabled = kw.get("two_factor_enabled", False)
    u.two_factor_secret = kw.get("two_factor_secret")
    u.notification_preferences = kw.get("notification_preferences")
    u.avatar_url = kw.get("avatar_url")
    u.onboarding_completed = kw.get("onboarding_completed", False)
    u.created_at = kw.get("created_at", datetime.now(UTC))
    return u


def _form(username="u@x.io", password="pw123456") -> OAuth2PasswordRequestForm:
    return OAuth2PasswordRequestForm(
        grant_type="password",
        username=username,
        password=password,
        scope="",
    )


def _totp(secret: str, timestep_offset: int = 0) -> str:
    padding = "=" * (8 - len(secret) % 8) if len(secret) % 8 else ""
    key = base64.b32decode(secret + padding)
    ts = (int(time.time()) // 30) + timestep_offset
    h = hmac.new(key, struct.pack(">Q", ts), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Disable slowapi checks — tests exercise handler logic, not the limiter."""
    try:
        monkeypatch.setattr(auth.limiter, "enabled", False)
    except AttributeError:
        monkeypatch.setattr(auth.limiter, "_enabled", False)


@pytest.fixture
def redis_store(monkeypatch):
    """Swap _redis_client for an in-memory FakeRedis; returns the instance."""
    store = FakeRedis()

    async def fake():
        return store

    monkeypatch.setattr(auth, "_redis_client", fake)
    return store


# ---------------------------------------------------------------------------
# Lockout helpers
# ---------------------------------------------------------------------------


class TestLoginLockout:
    pytestmark = pytest.mark.asyncio

    async def test_fail_key_normalizes_email(self):
        assert auth._login_fail_key("  Foo@Bar.COM ") == "auth:login_fail:foo@bar.com"

    async def test_lockout_zero_when_no_failures(self, redis_store):
        assert await auth._login_lockout_remaining("a@b.c") == 0

    async def test_lockout_zero_below_max(self, redis_store):
        redis_store.store[auth._login_fail_key("a@b.c")] = str(auth._LOGIN_MAX_FAILURES - 1)
        assert await auth._login_lockout_remaining("a@b.c") == 0

    async def test_lockout_returns_ttl_at_max(self, redis_store):
        key = auth._login_fail_key("a@b.c")
        redis_store.store[key] = str(auth._LOGIN_MAX_FAILURES)
        redis_store.ttls[key] = 321
        assert await auth._login_lockout_remaining("a@b.c") == 321

    async def test_lockout_falls_back_to_default_ttl(self, redis_store):
        key = auth._login_fail_key("a@b.c")
        redis_store.store[key] = "99"
        redis_store.ttls[key] = -1
        assert await auth._login_lockout_remaining("a@b.c") == auth._LOGIN_LOCKOUT_S

    async def test_lockout_fails_open_on_redis_error(self, monkeypatch):
        async def dead():
            return FakeRedis(fail=True)

        monkeypatch.setattr(auth, "_redis_client", dead)
        assert await auth._login_lockout_remaining("a@b.c") == 0

    async def test_record_failure_increments_and_sets_expiry_once(self, redis_store):
        key = auth._login_fail_key("a@b.c")
        await auth._record_login_failure("a@b.c")
        assert redis_store.store[key] == "1"
        assert redis_store.ttls[key] == auth._LOGIN_LOCKOUT_S
        redis_store.ttls.clear()
        await auth._record_login_failure("a@b.c")
        assert redis_store.store[key] == "2"
        assert key not in redis_store.ttls  # expiry only on first failure

    async def test_record_failure_survives_redis_error(self, monkeypatch):
        async def dead():
            return FakeRedis(fail=True)

        monkeypatch.setattr(auth, "_redis_client", dead)
        await auth._record_login_failure("a@b.c")  # must not raise

    async def test_clear_failures(self, redis_store):
        key = auth._login_fail_key("a@b.c")
        redis_store.store[key] = "5"
        await auth._clear_login_failures("a@b.c")
        assert key not in redis_store.store

    async def test_clear_failures_survives_redis_error(self, monkeypatch):
        async def dead():
            return FakeRedis(fail=True)

        monkeypatch.setattr(auth, "_redis_client", dead)
        await auth._clear_login_failures("a@b.c")  # must not raise


# ---------------------------------------------------------------------------
# Refresh-token rotation
# ---------------------------------------------------------------------------


class TestRefreshRotation:
    pytestmark = pytest.mark.asyncio

    async def test_replay_returns_cached_pair(self, redis_store):
        pair = auth.TokenResponse(access_token="a1", refresh_token="r1")
        redis_store.store["auth:rt_rotated:j1"] = pair.model_dump_json()
        out = await auth._replay_rotated_refresh("j1")
        assert out.access_token == "a1" and out.refresh_token == "r1"

    async def test_replay_burned_jti_raises_401(self, redis_store):
        redis_store.store["auth:rt_used:j2"] = "1"
        with pytest.raises(HTTPException) as e:
            await auth._replay_rotated_refresh("j2")
        assert e.value.status_code == 401

    async def test_replay_unknown_jti_returns_none(self, redis_store):
        assert await auth._replay_rotated_refresh("j3") is None

    async def test_replay_fails_open_on_redis_error(self, monkeypatch):
        async def dead():
            return FakeRedis(fail=True)

        monkeypatch.setattr(auth, "_redis_client", dead)
        assert await auth._replay_rotated_refresh("j4") is None

    async def test_record_rotation_writes_both_keys(self, redis_store):
        pair = auth.TokenResponse(access_token="a", refresh_token="r")
        exp = int(datetime.now(UTC).timestamp()) + 3600
        await auth._record_refresh_rotation("j5", pair, exp)
        keys = {k for k, _, _ in redis_store.sets}
        assert "auth:rt_rotated:j5" in keys and "auth:rt_used:j5" in keys
        grace = next(s for s in redis_store.sets if s[0].endswith("rotated:j5"))
        assert grace[2] == auth._REFRESH_GRACE_S

    async def test_record_rotation_survives_redis_error(self, monkeypatch):
        async def dead():
            return FakeRedis(fail=True)

        monkeypatch.setattr(auth, "_redis_client", dead)
        pair = auth.TokenResponse(access_token="a", refresh_token="r")
        await auth._record_refresh_rotation("j6", pair, 0)  # must not raise


# ---------------------------------------------------------------------------
# Scope + account-type helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_linkedin_scopes_base(self, monkeypatch):
        monkeypatch.setattr(auth, "get_settings", lambda: type("S", (), {"LINKEDIN_EXTRA_SCOPES": ""})())
        scopes = auth._linkedin_scopes()
        for s in ("openid", "profile", "email", "w_member_social", "w_organization_social"):
            assert s in scopes

    def test_linkedin_scopes_extra_dedup(self, monkeypatch):
        s = type("S", (), {"LINKEDIN_EXTRA_SCOPES": "r_ads_reporting, openid"})()
        monkeypatch.setattr(auth, "get_settings", lambda: s)
        scopes = auth._linkedin_scopes()
        assert "r_ads_reporting" in scopes
        assert scopes.count("openid") == 1

    def test_granted_scopes_string(self):
        out = auth._granted_scopes({"scope": "tweet.read tweet.write"}, ["x"])
        assert out == ["tweet.read", "tweet.write"]

    def test_granted_scopes_comma_and_list(self):
        assert auth._granted_scopes({"scope": "a,b c"}, []) == ["a", "b", "c"]
        assert auth._granted_scopes({"scope": ["b", "a"]}, []) == ["a", "b"]

    def test_granted_scopes_fallback_to_requested(self):
        assert auth._granted_scopes({}, ["r1"]) == ["r1"]
        assert auth._granted_scopes({"scope": "  "}, ["r1"]) == ["r1"]
        assert auth._granted_scopes("not-a-dict", ["r1"]) == ["r1"]

    def test_platform_account_type_matrix(self):
        assert auth._platform_account_type("linkedin", "x", {}, {}) == ("person", False)
        assert auth._platform_account_type("instagram", "x", {}, {}) == ("business", True)
        assert auth._platform_account_type("whatsapp", "x", {}, {}) == ("business", True)
        assert auth._platform_account_type("tiktok", "x", {}, {}) == ("person", False)
        assert auth._platform_account_type("threads", "x", {}, {}) == ("person", False)

    def test_platform_account_type_facebook_page_vs_user(self):
        ctx = {"fb_info": {"id": "user-1"}}
        assert auth._platform_account_type("facebook", "page-9", {}, ctx) == ("page", True)
        assert auth._platform_account_type("facebook", "user-1", {}, ctx) == ("user", False)
        assert auth._platform_account_type("facebook", "page-9", {}, {}) == ("user", False)

    def test_verify_totp_accepts_current_code(self):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        assert auth._verify_totp(secret, _totp(secret)) is True

    def test_verify_totp_rejects_wrong_code(self):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        bad = "000000" if _totp(secret) != "000000" else "999999"
        assert auth._verify_totp(secret, bad) is False

    def test_verify_totp_rejects_bad_secret(self):
        assert auth._verify_totp("!!!not-base32!!!", "123456") is False

    def test_verify_totp_accepts_adjacent_window(self):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        # code from previous 30s step still verifies within window=1
        assert auth._verify_totp(secret, _totp(secret, -1)) is True


# ---------------------------------------------------------------------------
# Register / login / refresh
# ---------------------------------------------------------------------------


class TestRegister:
    pytestmark = pytest.mark.asyncio

    async def test_duplicate_email_400(self):
        u = _user()
        with pytest.raises(HTTPException) as e:
            await auth.register(_request(), auth.UserCreate(email=u.email, password="x" * 8), FakeDB(users=[u]))
        assert e.value.status_code == 400

    async def test_creates_user_team_member(self):
        db = FakeDB()
        out = await auth.register(
            _request(),
            auth.UserCreate(email="new@x.io", password="pw123456", name="N", discount_code=" save20 "),
            db,
        )
        assert out.email == "new@x.io"
        user = next(o for o in db.added if isinstance(o, User))
        team = next(o for o in db.added if isinstance(o, Team))
        member = next(o for o in db.added if isinstance(o, TeamMember))
        assert team.owner_id == user.id
        assert team.polar_discount_code == "SAVE20"
        assert member.user_id == user.id and member.role == UserRole.OWNER
        assert db.commits == 1


class TestLogin:
    pytestmark = pytest.mark.asyncio

    async def test_locked_out_429(self, redis_store):
        redis_store.store[auth._login_fail_key("u@x.io")] = str(auth._LOGIN_MAX_FAILURES)
        with pytest.raises(HTTPException) as e:
            await auth.login(_request(), _form(), FakeDB(), otp=None)
        assert e.value.status_code == 429

    async def test_bad_credentials_401_and_records_failure(self, redis_store):
        with pytest.raises(HTTPException) as e:
            await auth.login(_request(), _form(password="wrong"), FakeDB(users=[_user()]), otp=None)
        assert e.value.status_code == 401
        assert redis_store.store[auth._login_fail_key("u@x.io")] == "1"

    async def test_unknown_email_401(self, redis_store):
        with pytest.raises(HTTPException) as e:
            await auth.login(_request(), _form("ghost@x.io"), FakeDB(), otp=None)
        assert e.value.status_code == 401

    async def test_2fa_required_returns_marker(self, redis_store):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        u = _user(two_factor_enabled=True, two_factor_secret=secret)
        with pytest.raises(HTTPException) as e:
            await auth.login(_request(), _form(), FakeDB(users=[u]), otp=None)
        assert e.value.detail == "two_factor_required"

    async def test_2fa_bad_code_401(self, redis_store):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        u = _user(two_factor_enabled=True, two_factor_secret=secret)
        bad = "000000" if _totp(secret) != "000000" else "999999"
        with pytest.raises(HTTPException) as e:
            await auth.login(_request(), _form(), FakeDB(users=[u]), otp=bad)
        assert e.value.detail == "Invalid 2FA code"

    async def test_success_returns_tokens_and_team_claim(self, redis_store):
        u = _user()
        team = Team(id=uuid.uuid4(), name="t", owner_id=u.id)
        db = FakeDB(users=[u], team_ids=[team.id])
        out = await auth.login(_request(), _form(), db, otp=None)
        payload = decode_token(out.access_token)
        assert payload["sub"] == str(u.id)
        assert payload["team_id"] == str(team.id)
        assert decode_token(out.refresh_token)["type"] == "refresh"
        assert auth._login_fail_key("u@x.io") not in redis_store.store

    async def test_success_with_valid_2fa(self, redis_store):
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        u = _user(two_factor_enabled=True, two_factor_secret=secret)
        out = await auth.login(_request(), _form(), FakeDB(users=[u]), otp=_totp(secret))
        assert out.access_token


class TestRefresh:
    pytestmark = pytest.mark.asyncio

    async def test_invalid_token_401(self):
        with pytest.raises(HTTPException) as e:
            await auth.refresh_token(auth.RefreshRequest(refresh_token="garbage"), FakeDB())
        assert e.value.status_code == 401

    async def test_access_token_rejected(self):
        tok = create_access_token({"sub": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.refresh_token(auth.RefreshRequest(refresh_token=tok), FakeDB())
        assert e.value.status_code == 401

    async def test_replay_returns_cached_pair(self, redis_store):
        u = _user()
        rt = create_refresh_token({"sub": str(u.id)})
        jti = decode_token(rt)["jti"]
        cached = auth.TokenResponse(access_token="cached_a", refresh_token="cached_r")
        redis_store.store[f"auth:rt_rotated:{jti}"] = cached.model_dump_json()
        out = await auth.refresh_token(auth.RefreshRequest(refresh_token=rt), FakeDB(users=[u]))
        assert out.access_token == "cached_a"

    async def test_rotation_issues_new_pair_and_burns_jti(self, redis_store):
        u = _user()
        team_id = uuid.uuid4()
        rt = create_refresh_token({"sub": str(u.id)})
        out = await auth.refresh_token(
            auth.RefreshRequest(refresh_token=rt),
            FakeDB(users=[u], team_ids=[team_id]),
        )
        assert out.access_token != rt
        jti = decode_token(rt)["jti"]
        assert f"auth:rt_used:{jti}" in redis_store.store
        assert decode_token(out.access_token)["team_id"] == str(team_id)

    async def test_unknown_user_401(self, redis_store):
        rt = create_refresh_token({"sub": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.refresh_token(auth.RefreshRequest(refresh_token=rt), FakeDB())
        assert e.value.status_code == 401


# ---------------------------------------------------------------------------
# Profile / team / password routes
# ---------------------------------------------------------------------------


class TestProfileRoutes:
    pytestmark = pytest.mark.asyncio

    async def test_get_me(self):
        u = _user()
        assert await auth.get_me(u) is u

    async def test_switch_team_forbidden_for_non_member(self):
        u = _user()
        with pytest.raises(HTTPException) as e:
            await auth.switch_team(auth.SwitchTeamRequest(team_id=uuid.uuid4()), u, FakeDB())
        assert e.value.status_code == 403

    async def test_switch_team_issues_team_scoped_token(self):
        u = _user()
        team_id = uuid.uuid4()
        m = TeamMember(team_id=team_id, user_id=u.id, role=UserRole.EDITOR)
        out = await auth.switch_team(
            auth.SwitchTeamRequest(team_id=team_id),
            u,
            FakeDB(memberships=[m]),
        )
        payload = decode_token(out.access_token)
        assert payload["team_id"] == str(team_id)

    async def test_update_profile_fields(self):
        u = _user()
        db = FakeDB()
        out = await auth.update_profile(
            auth.UpdateProfileRequest(
                full_name="New Name",
                avatar_url="https://x/a.png",
                timezone="Europe/Athens",
                onboarding_completed=True,
            ),
            u,
            db,
        )
        assert u.name == "New Name" and u.timezone == "Europe/Athens"
        assert u.onboarding_completed is True and db.commits == 1
        assert out is u

    async def test_change_password_wrong_current_400(self):
        u = _user()
        with pytest.raises(HTTPException) as e:
            await auth.change_password(
                auth.ChangePasswordRequest(current_password="nope", new_password="new12345"),
                u,
                FakeDB(),
            )
        assert e.value.status_code == 400

    async def test_change_password_success(self):
        u = _user()
        db = FakeDB()
        await auth.change_password(
            auth.ChangePasswordRequest(current_password="pw123456", new_password="brand-new-pw"),
            u,
            db,
        )
        from app.core.security import verify_password

        assert verify_password("brand-new-pw", u.password_hash)


# ---------------------------------------------------------------------------
# 2FA routes
# ---------------------------------------------------------------------------


class TestTwoFactor:
    pytestmark = pytest.mark.asyncio

    async def test_setup_returns_secret_and_uri(self):
        u = _user()
        db = FakeDB()
        out = await auth.setup_2fa(u, db)
        assert out.secret and out.secret in out.qr_uri
        assert out.qr_uri.startswith("otpauth://totp/SocialAuto:")
        assert u.two_factor_secret == out.secret and db.commits == 1

    async def test_verify_requires_setup_first(self):
        u = _user()
        with pytest.raises(HTTPException) as e:
            await auth.verify_2fa(auth.TwoFactorVerifyRequest(code="123456"), u, FakeDB())
        assert e.value.status_code == 400

    async def test_verify_enables_with_valid_code(self):
        u = _user()
        db = FakeDB()
        setup = await auth.setup_2fa(u, db)
        out = await auth.verify_2fa(
            auth.TwoFactorVerifyRequest(code=_totp(setup.secret)),
            u,
            db,
        )
        assert out["enabled"] is True and u.two_factor_enabled is True

    async def test_verify_rejects_bad_code(self):
        u = _user(two_factor_secret=base64.b32encode(b"12345678901234567890").decode().rstrip("="))
        bad = "000000" if _totp(u.two_factor_secret) != "000000" else "999999"
        with pytest.raises(HTTPException) as e:
            await auth.verify_2fa(auth.TwoFactorVerifyRequest(code=bad), u, FakeDB())
        assert e.value.status_code == 400

    async def test_disable_requires_password(self):
        u = _user(two_factor_enabled=True)
        with pytest.raises(HTTPException):
            await auth.disable_2fa(
                auth.TwoFactorDisableRequest(current_password="wrong"),
                u,
                FakeDB(),
            )
        assert u.two_factor_enabled is True

    async def test_disable_clears_secret(self):
        u = _user(two_factor_enabled=True, two_factor_secret="ABC")
        out = await auth.disable_2fa(
            auth.TwoFactorDisableRequest(current_password="pw123456"),
            u,
            FakeDB(),
        )
        assert out["enabled"] is False
        assert u.two_factor_enabled is False and u.two_factor_secret is None


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


class TestNotificationPrefs:
    pytestmark = pytest.mark.asyncio

    async def test_defaults(self):
        out = await auth.get_notification_preferences(_user())
        assert out.email_new_post is True and out.email_analytics is False
        assert out.push_new_post is True and out.push_scheduled is False

    async def test_custom_values(self):
        u = _user(notification_preferences={"email_analytics": True, "push_new_post": False})
        out = await auth.get_notification_preferences(u)
        assert out.email_analytics is True and out.push_new_post is False

    async def test_update_persists(self):
        u = _user()
        db = FakeDB()
        out = await auth.update_notification_preferences(
            auth.NotificationPreferencesRequest(email_analytics=True, push_scheduled=True),
            u,
            db,
        )
        assert u.notification_preferences["email_analytics"] is True
        assert out.push_scheduled is True and db.commits == 1


# ---------------------------------------------------------------------------
# Export / delete / password reset
# ---------------------------------------------------------------------------


class TestExportDeleteReset:
    pytestmark = pytest.mark.asyncio

    async def test_export_no_team_returns_empty(self, monkeypatch):
        u = _user()
        tok = create_access_token({"sub": str(u.id)})

        async def no_team(db, user):
            return None

        import app.api.deps as deps

        monkeypatch.setattr(deps, "get_user_team", no_team)
        out = await auth.export_user_data(u, FakeDB(), tok)
        assert out["posts"] == [] and out["accounts"] == []

    async def test_export_with_team_payload(self, monkeypatch):
        u = _user()
        team = Team(id=uuid.uuid4(), name="t", owner_id=u.id)
        post = type(
            "P",
            (),
            {
                "id": uuid.uuid4(),
                "content_text": "hi",
                "status": "published",
                "created_at": datetime.now(UTC),
                "scheduled_at": None,
            },
        )()
        acct = SocialAccount(team_id=team.id, platform="linkedin", account_id="1", username="@x", display_name="X", status="active")
        tok = create_access_token({"sub": str(u.id), "team_id": str(team.id)})
        m = TeamMember(team_id=team.id, user_id=u.id, role=UserRole.OWNER)
        db = FakeDB(memberships=[m], teams=[team], posts=[post], accounts=[acct])
        out = await auth.export_user_data(u, db, tok)
        assert out["posts"][0]["content_text"] == "hi"
        assert out["accounts"][0]["platform"] == "linkedin"
        assert out["user"]["email"] == u.email

    async def test_delete_account_wrong_password(self):
        u = _user()
        with pytest.raises(HTTPException):
            await auth.delete_account(auth.DeleteAccountRequest(password="nope"), u, FakeDB())

    async def test_delete_account_removes_everything(self):
        u = _user()
        team = Team(id=uuid.uuid4(), name="t", owner_id=u.id)
        m = TeamMember(team_id=team.id, user_id=u.id, role=UserRole.OWNER)
        db = FakeDB(memberships=[m], teams=[team])
        await auth.delete_account(auth.DeleteAccountRequest(password="pw123456"), u, db)
        assert team in db.deleted and m in db.deleted and u in db.deleted
        assert db.commits == 1

    async def test_forgot_password_never_enumerates(self):
        out = await auth.forgot_password(
            _request(),
            auth.ForgotPasswordRequest(email="ghost@x.io"),
            FakeDB(),
        )
        assert "reset link" in out["message"]

    async def test_forgot_password_known_email(self, monkeypatch):
        u = _user()
        sent = []

        async def fake_send(user, link):
            sent.append(link)

        import asyncio as _a

        real_create_task = _a.create_task
        monkeypatch.setattr(_a, "create_task", lambda coro: (sent.append(coro), real_create_task(coro))[1])
        out = await auth.forgot_password(
            _request(),
            auth.ForgotPasswordRequest(email=u.email),
            FakeDB(users=[u]),
        )
        assert "reset link" in out["message"]

    async def test_reset_password_invalid_token_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.reset_password(
                _request(),
                auth.ResetPasswordRequest(token="bad", new_password="x" * 8),
                FakeDB(),
            )
        assert e.value.status_code == 400

    async def test_reset_password_success(self):
        u = _user()
        tok = create_reset_token({"sub": str(u.id), "email": u.email})
        db = FakeDB(users=[u])
        out = await auth.reset_password(
            _request(),
            auth.ResetPasswordRequest(token=tok, new_password="newpass99"),
            db,
        )
        from app.core.security import verify_password

        assert verify_password("newpass99", u.password_hash)
        assert "reset" in out["message"].lower()


# ---------------------------------------------------------------------------
# OAuth authorize
# ---------------------------------------------------------------------------


class TestOAuthAuthorize:
    pytestmark = pytest.mark.asyncio

    async def test_unconfigured_platform_400(self, monkeypatch):
        empty = type("C", (), {"client_id": "", "client_secret": ""})()
        monkeypatch.setattr(auth, "threads_client", empty)
        monkeypatch.setattr(auth.settings, "THREADS_REDIRECT_URI", "https://x/cb")
        with pytest.raises(HTTPException) as e:
            await auth.oauth_authorize("threads", uuid.uuid4(), _user())
        assert e.value.status_code == 400 and "not configured" in e.value.detail

    async def test_linkedin_missing_secret_400(self, monkeypatch):
        c = type("C", (), {"client_id": "lid", "client_secret": ""})()
        monkeypatch.setattr(auth, "linkedin_client", c)
        monkeypatch.setattr(auth.settings, "LINKEDIN_REDIRECT_URI", "https://x/cb")
        with pytest.raises(HTTPException) as e:
            await auth.oauth_authorize("linkedin", uuid.uuid4(), _user())
        assert "LINKEDIN_CLIENT_SECRET" in e.value.detail

    async def test_twitter_authorize_carries_pkce(self, monkeypatch):
        captured = {}

        class FakeClient:
            client_id = "tid"
            client_secret = "tsec"

            async def get_authorization_url(self, redirect_uri, **kw):
                captured.update(kw)
                return "https://x.com/auth?x=1"

        monkeypatch.setattr(auth, "twitter_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        team_id = uuid.uuid4()
        out = await auth.oauth_authorize("twitter", team_id, _user())
        assert out["authorization_url"].startswith("https://x.com")
        assert captured["code_challenge"] and captured["code_challenge_method"] == "S256"
        from app.core.security import verify_oauth_state

        state = verify_oauth_state(captured["state"])
        assert state["t"] == str(team_id) and state["cv"]

    async def test_tiktok_authorize_uses_client_key(self, monkeypatch):
        captured = {}

        class FakeClient:
            client_id = "ttkey"
            client_secret = "ttsec"

            async def get_authorization_url(self, redirect_uri, **kw):
                captured.update(kw)
                return "https://tiktok/auth"

        monkeypatch.setattr(auth, "tiktok_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TIKTOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.settings, "TIKTOK_CLIENT_KEY", "ttkey")
        await auth.oauth_authorize("tiktok", uuid.uuid4(), _user())
        extras = captured["extras_params"]
        assert extras["client_key"] == "ttkey"
        assert "," in extras["scope"] and "video.publish" in extras["scope"]
        assert captured["scope"] is None  # library must not join scopes

    async def test_instagram2_authorize_delegates(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "INSTAGRAM2_CLIENT_ID", "ig2id")
        monkeypatch.setattr(auth.settings, "INSTAGRAM2_REDIRECT_URI", "https://x/cb")
        out = await auth.oauth_authorize("instagram2", uuid.uuid4(), _user())
        assert "instagram_business_basic" in out["authorization_url"]

    async def test_instagram_onboarding_delegates(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "FACEBOOK_CLIENT_ID", "fbid")
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        out = await auth.oauth_authorize("instagram-onboarding", uuid.uuid4(), _user())
        assert "IG_API_ONBOARDING" in out["authorization_url"] or "instagram_basic" in out["authorization_url"]


# ---------------------------------------------------------------------------
# OAuth callback dispatch + error paths
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    pytestmark = pytest.mark.asyncio

    async def test_error_param_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("twitter", state="x", db=FakeDB(), error="denied", error_description="no")
        assert "denied" in e.value.detail

    async def test_missing_code_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("twitter", state=sign_oauth_state({"t": str(uuid.uuid4())}), db=FakeDB())
        assert "No authorization code" in e.value.detail

    async def test_tampered_state_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("twitter", state="deadbeef", db=FakeDB(), code="c")
        assert "tampered" in e.value.detail

    async def test_instagram_onboarding_wrong_path_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("instagram-onboarding", state="x", db=FakeDB(), code="c")
        assert e.value.status_code == 400

    async def test_token_exchange_failure_400(self, monkeypatch):
        class BadClient:
            async def get_access_token(self, *a, **kw):
                raise RuntimeError("provider down")

        monkeypatch.setattr(auth, "twitter_client", BadClient())
        monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        state = sign_oauth_state({"t": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("twitter", state=state, db=FakeDB(), code="c")
        assert "Token exchange failed" in e.value.detail


class _FakeAsyncHTTPClient:
    """Async-context httpx.AsyncClient stand-in routing URLs to responses."""

    def __init__(self, routes, *a, **kw):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return _FakeResp(404, {})

    async def post(self, url, **kw):
        return await self.get(url, **kw)


@pytest.fixture
def _silence_connect_email(monkeypatch):
    async def fake_send(owner, platform):
        return True

    import app.services.email_templates as et

    monkeypatch.setattr(et, "send_account_connected_email", fake_send)


class TestOAuthCallbackHappyPath:
    pytestmark = pytest.mark.asyncio

    async def test_twitter_callback_creates_account(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, code, redirect_uri, code_verifier=None):
                return {"access_token": "at", "refresh_token": "rt", "scope": "tweet.read tweet.write offline.access", "expires_in": 7200}

        http = _FakeAsyncHTTPClient(
            {
                "https://api.x.com/2/users/me": _FakeResp(
                    200,
                    {
                        "data": {"id": "42", "username": "tbaltzakis", "name": "T", "profile_image_url": "p"},
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "twitter_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        owner = _user()
        db = FakeDB(users=[owner])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("twitter", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "twitter" and acct.account_id == "42"
        assert acct.status == "active" and acct.scopes == ["offline.access", "tweet.read", "tweet.write"]
        assert acct.token_expires_at is not None

    async def test_linkedin_callback_creates_person_and_syncs_orgs(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, code, redirect_uri, code_verifier=None):
                return {"access_token": "li_at", "refresh_token": "li_rt", "expires_in": 5000}

        http = _FakeAsyncHTTPClient(
            {
                "https://api.linkedin.com/v2/userinfo": _FakeResp(
                    200,
                    {
                        "sub": "li-sub-1",
                        "email": "u@x.io",
                        "given_name": "T",
                        "family_name": "B",
                    },
                ),
                "https://api.linkedin.com/rest/organizationAcls": _FakeResp(
                    200,
                    {
                        "elements": [{"organization": "urn:li:organization:777", "role": "ADMINISTRATOR"}],
                    },
                ),
                "https://api.linkedin.com/rest/organizations/777": _FakeResp(
                    200,
                    {
                        "localizedName": "Cloudless.gr",
                        "vanityName": "cloudless-gr",
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "linkedin_client", FakeClient())
        monkeypatch.setattr(auth.settings, "LINKEDIN_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        owner = _user()
        db = FakeDB(users=[owner])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("linkedin", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        accounts = [a for a in db.added if isinstance(a, SocialAccount)]
        person = next(a for a in accounts if a.account_id == "li-sub-1")
        org = next(a for a in accounts if a.account_id == "777")
        assert person.account_type == "person" and org.is_business is True
        assert org.display_name == "Cloudless.gr"

    async def test_linkedin_callback_userinfo_failure_400(self, monkeypatch, _silence_connect_email):
        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "li_at"}

        http = _FakeAsyncHTTPClient(
            {
                "https://api.linkedin.com/v2/userinfo": _FakeResp(500, {"err": "down"}),
            }
        )
        monkeypatch.setattr(auth, "linkedin_client", FakeClient())
        monkeypatch.setattr(auth.settings, "LINKEDIN_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        state = sign_oauth_state({"t": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("linkedin", state=state, db=FakeDB(), code="c")
        assert "LinkedIn profile fetch failed" in e.value.detail

    async def test_twitter_callback_402_explains_credits(self, monkeypatch, _silence_connect_email):
        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "at"}

        http = _FakeAsyncHTTPClient(
            {
                "https://api.x.com/2/users/me": _FakeResp(402, {"detail": "credits depleted"}),
            }
        )
        monkeypatch.setattr(auth, "twitter_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        state = sign_oauth_state({"t": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("twitter", state=state, db=FakeDB(), code="c")
        assert "402" in e.value.detail and "credits" in e.value.detail

    async def test_existing_account_updates_tokens(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        existing = SocialAccount(
            team_id=team_id,
            platform="twitter",
            account_id="42",
            username="old",
            status="expired",
            meta_data={},
        )

        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "new_at", "expires_in": 7200}

        http = _FakeAsyncHTTPClient(
            {
                "https://api.x.com/2/users/me": _FakeResp(
                    200,
                    {
                        "data": {"id": "42", "username": "tbaltzakis", "name": "T"},
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "twitter_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TWITTER_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(accounts=[existing])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("twitter", state=state, db=db, code="c")
        assert "connected" in out["message"]
        assert existing.status == "active" and existing.username == "tbaltzakis"


# ---------------------------------------------------------------------------
# Instagram onboarding / business-login authorize
# ---------------------------------------------------------------------------


class TestInstagramFlows:
    pytestmark = pytest.mark.asyncio

    async def test_onboarding_authorize_not_configured(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "FACEBOOK_CLIENT_ID", "")
        with pytest.raises(HTTPException) as e:
            await auth.instagram_onboarding_authorize(uuid.uuid4(), _user())
        assert e.value.status_code == 400

    async def test_onboarding_authorize_url(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "FACEBOOK_CLIENT_ID", "fbid")
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        team_id = uuid.uuid4()
        out = await auth.instagram_onboarding_authorize(team_id, _user())
        url = out["authorization_url"]
        assert "facebook.com/dialog/oauth" in url and "IG_API_ONBOARDING" in url
        assert "display=page" in url

    async def test_instagram2_authorize_not_configured(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "INSTAGRAM2_CLIENT_ID", "")
        with pytest.raises(HTTPException) as e:
            await auth.instagram2_authorize(uuid.uuid4(), _user())
        assert e.value.status_code == 400

    async def test_instagram2_authorize_url(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "INSTAGRAM2_CLIENT_ID", "ig2id")
        monkeypatch.setattr(auth.settings, "INSTAGRAM2_REDIRECT_URI", "https://x/cb")
        out = await auth.instagram2_authorize(uuid.uuid4(), _user())
        url = out["authorization_url"]
        assert "instagram.com/oauth/authorize" in url
        assert "instagram_business_content_publish" in url

    async def test_instagram2_callback_error_400(self):
        with pytest.raises(HTTPException):
            await auth.instagram2_callback(state="x", db=FakeDB(), error="err", error_description="d")

    async def test_instagram2_callback_bad_state_400(self):
        with pytest.raises(HTTPException) as e:
            await auth.instagram2_callback(state="tampered", db=FakeDB(), code="c")
        assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# LinkedIn org sync
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeHTTP:
    """Routes URL prefixes to canned responses."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, **kw):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return _FakeResp(404, {})


class TestLinkedInOrgSync:
    pytestmark = pytest.mark.asyncio

    async def test_sync_upserts_org_accounts(self):
        team_id = uuid.uuid4()
        http = _FakeHTTP(
            {
                "https://api.linkedin.com/rest/organizationAcls": _FakeResp(
                    200,
                    {
                        "elements": [
                            {"organization": "urn:li:organization:777", "role": "ADMINISTRATOR"},
                            {"organization": "bad-urn", "role": "ADMINISTRATOR"},
                        ],
                    },
                ),
                "https://api.linkedin.com/rest/organizations/777": _FakeResp(
                    200,
                    {
                        "localizedName": "Cloudless.gr",
                        "vanityName": "cloudless-gr",
                    },
                ),
            }
        )
        db = FakeDB()
        out = await auth._sync_linkedin_organizations(
            db=db,
            team_id=team_id,
            access_token="at",
            refresh_token="rt",
            scopes=["openid"],
            http=http,
        )
        assert len(out) == 1
        acct = out[0]
        assert acct.platform == "linkedin" and acct.account_id == "777"
        assert acct.display_name == "Cloudless.gr"
        assert acct.account_type == "organization" and acct.is_business is True
        assert acct.meta_data["author_urn"] == "urn:li:organization:777"
        assert db.flushes == 1

    async def test_sync_updates_existing_account(self):
        team_id = uuid.uuid4()
        existing = SocialAccount(
            team_id=team_id,
            platform="linkedin",
            account_id="777",
            username="old",
            display_name="Old",
            status="expired",
            account_type="organization",
            is_business=False,
            meta_data={},
        )
        http = _FakeHTTP(
            {
                "https://api.linkedin.com/rest/organizationAcls": _FakeResp(
                    200,
                    {
                        "elements": [{"organization": "urn:li:organization:777", "role": "ADMINISTRATOR"}],
                    },
                ),
                "https://api.linkedin.com/rest/organizations/777": _FakeResp(200, {"name": "New Name"}),
            }
        )
        db = FakeDB(accounts=[existing])
        out = await auth._sync_linkedin_organizations(
            db=db,
            team_id=team_id,
            access_token="at2",
            refresh_token=None,
            scopes=["s"],
            http=http,
        )
        assert out[0] is existing
        assert existing.display_name == "New Name"
        assert existing.status == "active" and existing.is_business is True

    async def test_sync_empty_when_no_acls(self):
        http = _FakeHTTP(
            {
                "https://api.linkedin.com/rest/organizationAcls": _FakeResp(200, {"elements": []}),
                "https://api.linkedin.com/v2/organizationAcls": _FakeResp(200, {"elements": []}),
            }
        )
        out = await auth._sync_linkedin_organizations(
            db=FakeDB(),
            team_id=uuid.uuid4(),
            access_token="at",
            refresh_token=None,
            scopes=[],
            http=http,
        )
        assert out == []


# ---------------------------------------------------------------------------
# TikTokOAuth2 — client_key wire format
# ---------------------------------------------------------------------------


class TestTikTokOAuth:
    pytestmark = pytest.mark.asyncio

    async def test_get_access_token_sends_client_key(self, monkeypatch):
        captured = {}

        class FakeHTTPX:
            def build_request(self, method, url, data=None, headers=None):
                captured["data"] = data
                return ("req", url)

        client = auth.TikTokOAuth2(
            "tt_key",
            "tt_secret",
            authorize_endpoint="https://auth",
            access_token_endpoint="https://token",
            name="tiktok",
        )
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_httpx():
            yield FakeHTTPX()

        async def fake_send(c, request, auth=None, exc_class=None):
            return type("R", (), {"json": lambda s: {"access_token": "at"}, "text": "{}", "status_code": 200, "headers": {}})()

        monkeypatch.setattr(client, "get_httpx_client", fake_httpx)
        monkeypatch.setattr(client, "send_request", fake_send)
        monkeypatch.setattr(client, "get_json", lambda resp, exc_class=None: resp.json())
        token = await client.get_access_token("code123", "https://cb")
        assert captured["data"]["client_key"] == "tt_key"
        assert captured["data"]["grant_type"] == "authorization_code"
        assert token["access_token"] == "at"


class TestOAuthCallbackMorePlatforms:
    pytestmark = pytest.mark.asyncio

    async def test_threads_callback_exchanges_long_lived(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "short_at", "user_id": "tt-1"}

        http = _FakeAsyncHTTPClient(
            {
                "https://graph.threads.net/access_token": _FakeResp(
                    200,
                    {
                        "access_token": "long_at",
                        "expires_in": 5184000,
                    },
                ),
                "https://graph.threads.net/me": _FakeResp(
                    200,
                    {
                        "id": "tt-1",
                        "username": "cloudless.gr",
                        "name": "Cloudless",
                        "threads_profile_picture_url": "pic",
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "threads_client", FakeClient())
        monkeypatch.setattr(auth.settings, "THREADS_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.settings, "THREADS_CLIENT_SECRET", "cs")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("threads", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "threads" and acct.username == "cloudless.gr"
        assert "threads_content_publish" in acct.scopes
        assert acct.token_expires_at is not None  # 60-day long-lived expiry

    async def test_threads_callback_ll_exchange_failure_400(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "at"}

        http = _FakeAsyncHTTPClient(
            {
                "https://graph.threads.net/access_token": _FakeResp(200, {"error": "bad"}),
            }
        )
        monkeypatch.setattr(auth, "threads_client", FakeClient())
        monkeypatch.setattr(auth.settings, "THREADS_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.settings, "THREADS_CLIENT_SECRET", "cs")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        state = sign_oauth_state({"t": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("threads", state=state, db=FakeDB(), code="c")
        assert "long-lived token exchange failed" in e.value.detail

    async def test_instagram_callback_discovers_business_account(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "fb_at"}

        http = _FakeAsyncHTTPClient(
            {
                "https://graph.facebook.com/v26.0/me/accounts": _FakeResp(
                    200,
                    {
                        "data": [
                            {
                                "id": "pg-1",
                                "name": "cloudless.gr",
                                "access_token": "page_tok",
                                "instagram_business_account": {
                                    "id": "ig-9",
                                    "ig_id": "ig-9",
                                    "username": "cloudless.gr",
                                    "profile_picture_url": "igpic",
                                    "name": "Cloudless",
                                },
                            }
                        ],
                    },
                ),
                "https://graph.facebook.com/v26.0/me": _FakeResp(
                    200,
                    {
                        "id": "fb-1",
                        "name": "T",
                        "picture": {"data": {"url": "fbpic"}},
                    },
                ),
                "https://graph.facebook.com/v26.0/oauth/access_token": _FakeResp(
                    200,
                    {
                        "access_token": "ll_at",
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "instagram_client", FakeClient())
        monkeypatch.setattr(auth.settings, "INSTAGRAM_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("instagram", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.account_id == "ig-9" and acct.username == "cloudless.gr"
        assert "instagram_content_publish" in acct.scopes

    async def test_instagram_callback_personal_fallback(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        # No IG business account on any page or business → falls back to
        # the Facebook identity (personal IG connection).
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "fb_at"}

        http = _FakeAsyncHTTPClient(
            {
                "https://graph.facebook.com/v26.0/me/accounts": _FakeResp(
                    200,
                    {
                        "data": [{"id": "pg-1", "access_token": "pt"}],
                    },
                ),
                "https://graph.facebook.com/v26.0/me/businesses": _FakeResp(
                    200,
                    {
                        "data": [{"id": "biz-1"}],
                    },
                ),
                "https://graph.facebook.com/v26.0/biz-1/instagram_accounts": _FakeResp(
                    200,
                    {
                        "data": [],
                    },
                ),
                "https://graph.facebook.com/v26.0/pg-1/page_backed_instagram_accounts": _FakeResp(
                    200,
                    {
                        "data": [],
                    },
                ),
                "https://graph.facebook.com/v26.0/me": _FakeResp(
                    200,
                    {
                        "id": "fb-7",
                        "name": "Person",
                    },
                ),
                "https://graph.facebook.com/v26.0/oauth/access_token": _FakeResp(
                    200,
                    {
                        "access_token": "ll_at",
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "instagram_client", FakeClient())
        monkeypatch.setattr(auth.settings, "INSTAGRAM_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("instagram", state=state, db=db, code="c")
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.account_id == "fb-7" and acct.username == "Person"
        assert "connected" in out["message"]

    async def test_tiktok_callback_scope_gated_fields(
        self,
        monkeypatch,
        _silence_connect_email,
    ):
        team_id = uuid.uuid4()

        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {"access_token": "tt_at", "open_id": "oid-1", "scope": "user.info.basic user.info.profile video.publish"}

        http = _FakeAsyncHTTPClient(
            {
                "https://open.tiktokapis.com/v2/user/info/": _FakeResp(
                    200,
                    {
                        "data": {
                            "user": {
                                "open_id": "oid-1",
                                "display_name": "Cloudless",
                                "avatar_url": "av",
                                "username": "cloudless.gr",
                            }
                        },
                    },
                ),
            }
        )
        monkeypatch.setattr(auth, "tiktok_client", FakeClient())
        monkeypatch.setattr(auth.settings, "TIKTOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("tiktok", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "tiktok" and acct.account_id == "oid-1"
        assert "video.publish" in acct.scopes
        assert "user.info.stats" not in acct.scopes


class TestOAuthCallbackFacebookWhatsApp:
    pytestmark = pytest.mark.asyncio

    _FB = "https://graph.facebook.com/v26.0"

    def _fb_client(self):
        class FakeClient:
            async def get_access_token(self, *a, **kw):
                return {
                    "access_token": "short_at",
                    "scope": "public_profile,email,pages_show_list",
                }

        return FakeClient()

    def _fb_routes(self, **overrides):
        routes = {
            f"{self._FB}/me/accounts": _FakeResp(200, {"data": []}),
            f"{self._FB}/me/businesses": _FakeResp(200, {"data": []}),
            f"{self._FB}/me": _FakeResp(
                200,
                {
                    "id": "fb-1",
                    "name": "Themis",
                    "email": "t@x.io",
                    "picture": {"data": {"url": "pic"}},
                },
            ),
            f"{self._FB}/oauth/access_token": _FakeResp(200, {"access_token": "ll_at", "expires_in": 5184000}),
        }
        routes.update(overrides)
        return routes

    async def test_facebook_callback_creates_user_and_page_accounts(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/accounts": _FakeResp(
                        200,
                        {
                            "data": [
                                {"id": "pg1", "name": "cloudless.gr", "access_token": "pt1", "category": "App"},
                                {"id": "pg2", "name": "Blog", "access_token": "pt2"},
                            ]
                        },
                    ),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id)})
        out = await auth.oauth_callback("facebook", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        accounts = [a for a in db.added if isinstance(a, SocialAccount)]
        user_acct = next(a for a in accounts if a.account_id == "fb-1")
        pages = [a for a in accounts if a.account_id in ("pg1", "pg2")]
        assert user_acct.platform == "facebook" and user_acct.account_type == "user"
        assert len(pages) == 2
        assert all(p.account_type == "page" and p.is_business for p in pages)
        assert all(p.parent_account_id == user_acct.id for p in pages)
        assert user_acct.scopes == ["public_profile", "email", "pages_show_list"]
        assert db.commits >= 2  # main commit + pages commit

    async def test_facebook_callback_existing_page_updated(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        existing_page = SocialAccount(
            team_id=team_id,
            platform="facebook",
            account_id="pg1",
            username="old",
            status="revoked",
            meta_data={},
        )
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/accounts": _FakeResp(
                        200,
                        {"data": [{"id": "pg1", "name": "cloudless.gr", "access_token": "pt_new"}]},
                    ),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()], accounts=[existing_page])
        state = sign_oauth_state({"t": str(team_id)})
        await auth.oauth_callback("facebook", state=state, db=db, code="c")
        assert existing_page.status == "active"
        assert existing_page.username == "cloudless.gr"
        assert existing_page.meta_data["page_token"] == "pt_new"
        page_adds = [a for a in db.added if isinstance(a, SocialAccount) and a.account_id == "pg1"]
        assert page_adds == []  # updated, not re-created

    async def test_facebook_callback_me_error_400(self, monkeypatch, _silence_connect_email):
        http = _FakeAsyncHTTPClient(self._fb_routes(**{f"{self._FB}/me": _FakeResp(200, {"error": {"message": "bad token"}})}))
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        state = sign_oauth_state({"t": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("facebook", state=state, db=FakeDB(), code="c")
        assert e.value.status_code == 400
        assert "Facebook /me failed" in e.value.detail

    async def test_facebook_ig_onboarding_discovers_ig_account(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/accounts": _FakeResp(
                        200,
                        {
                            "data": [
                                {
                                    "id": "pg1",
                                    "name": "cloudless.gr",
                                    "access_token": "pt1",
                                    "instagram_business_account": {
                                        "id": "ig-9",
                                        "username": "cloudless.gr",
                                        "name": "Cloudless",
                                        "profile_picture_url": "igpic",
                                    },
                                }
                            ]
                        },
                    ),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id), "p": "instagram-onboarding"})
        out = await auth.oauth_callback("facebook", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "instagram" and acct.account_id == "ig-9"
        assert acct.username == "cloudless.gr"
        assert "instagram_content_publish" in acct.scopes

    async def test_facebook_ig_onboarding_page_backed_fallback(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/accounts": _FakeResp(
                        200,
                        {"data": [{"id": "pg1", "name": "P", "access_token": "pt1"}]},
                    ),
                    f"{self._FB}/pg1/page_backed_instagram_accounts": _FakeResp(
                        200,
                        {"data": [{"id": "ig-pb", "username": "cloudless.gr", "name": "Cloudless"}]},
                    ),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id), "p": "instagram-onboarding"})
        await auth.oauth_callback("facebook", state=state, db=db, code="c")
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "instagram" and acct.account_id == "ig-pb"

    async def test_facebook_ig_onboarding_no_ig_400(self, monkeypatch, _silence_connect_email):
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/accounts": _FakeResp(200, {"data": [{"id": "pg1", "access_token": "pt"}]}),
                    f"{self._FB}/pg1/page_backed_instagram_accounts": _FakeResp(200, {"data": []}),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        state = sign_oauth_state({"t": str(uuid.uuid4()), "p": "instagram-onboarding"})
        with pytest.raises(HTTPException) as e:
            await auth.oauth_callback("facebook", state=state, db=FakeDB(), code="c")
        assert "no Instagram Business account" in e.value.detail

    async def test_whatsapp_callback_discovers_waba_and_phones(self, monkeypatch, _silence_connect_email):
        team_id = uuid.uuid4()
        http = _FakeAsyncHTTPClient(
            self._fb_routes(
                **{
                    f"{self._FB}/me/businesses": _FakeResp(200, {"data": [{"id": "biz1", "name": "Cloudless"}]}),
                    f"{self._FB}/biz1/owned_whatsapp_business_accounts": _FakeResp(200, {"data": [{"id": "waba1"}]}),
                    f"{self._FB}/waba1/phone_numbers": _FakeResp(
                        200,
                        {
                            "data": [
                                {
                                    "id": "ph1",
                                    "display_phone_number": "+30 555",
                                    "verified_name": "Cloudless",
                                    "quality_rating": "GREEN",
                                }
                            ]
                        },
                    ),
                }
            )
        )
        monkeypatch.setattr(auth, "facebook_client", self._fb_client())
        monkeypatch.setattr(auth.settings, "FACEBOOK_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **kw: http)
        db = FakeDB(users=[_user()])
        state = sign_oauth_state({"t": str(team_id), "p": "whatsapp"})
        out = await auth.oauth_callback("facebook", state=state, db=db, code="c")
        assert "connected successfully" in out["message"]
        acct = next(a for a in db.added if isinstance(a, SocialAccount))
        assert acct.platform == "whatsapp"
        assert acct.meta_data["waba_id"] == "waba1"
        assert acct.meta_data["phone_numbers"][0]["id"] == "ph1"
        assert "whatsapp_business_messaging" in acct.scopes
        # No Facebook Page accounts should be created for a WhatsApp connect
        page_adds = [a for a in db.added if isinstance(a, SocialAccount) and a.platform == "facebook" and a.account_type == "page"]
        assert page_adds == []
