"""Coverage for app/api/deps.py — team-scoped dependencies."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.deps as D
from app.models.user import UserRole


class _Res:
    def __init__(self, scalar=None, first=None, one=None):
        self._scalar = scalar
        self._first = first
        self._one = one

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._one

    def scalars(self):
        return SimpleNamespace(all=lambda: [], first=lambda: self._first)


class _DB:
    """Queue-based fake session — one result per execute call."""

    def __init__(self, results):
        self._q = list(results)

    async def execute(self, *a, **kw):
        return self._q.pop(0)


def _user(**kw):
    d = dict(id=uuid.uuid4(), email="u@x.co")
    d.update(kw)
    return SimpleNamespace(**d)


class TestCurrentTeamId:
    @pytest.mark.asyncio
    async def test_jwt_team_valid(self, monkeypatch):
        tid = uuid.uuid4()
        monkeypatch.setattr(D, "decode_token", lambda t: {"team_id": str(tid)})
        db = _DB([_Res(scalar=object())])
        out = await D.get_current_team_id(_user(), db, token="tok")
        assert out == tid

    @pytest.mark.asyncio
    async def test_jwt_team_not_member_falls_back(self, monkeypatch):
        team = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(D, "decode_token", lambda t: {"team_id": str(uuid.uuid4())})
        monkeypatch.setattr(D, "get_user_team", AsyncMock(return_value=team))
        db = _DB([_Res(scalar=None)])
        out = await D.get_current_team_id(_user(), db, token="tok")
        assert out == team.id

    @pytest.mark.asyncio
    async def test_no_jwt_claim_fallback(self, monkeypatch):
        team = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(D, "decode_token", lambda t: {})
        monkeypatch.setattr(D, "get_user_team", AsyncMock(return_value=team))
        out = await D.get_current_team_id(_user(), _DB([]), token="tok")
        assert out == team.id

    @pytest.mark.asyncio
    async def test_decode_none_fallback(self, monkeypatch):
        team = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(D, "decode_token", lambda t: None)
        monkeypatch.setattr(D, "get_user_team", AsyncMock(return_value=team))
        out = await D.get_current_team_id(_user(), _DB([]), token="tok")
        assert out == team.id

    @pytest.mark.asyncio
    async def test_no_team_403(self, monkeypatch):
        monkeypatch.setattr(D, "decode_token", lambda t: None)
        monkeypatch.setattr(D, "get_user_team", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as ei:
            await D.get_current_team_id(_user(), _DB([]), token="tok")
        assert ei.value.status_code == 403


class TestGetUserTeam:
    @pytest.mark.asyncio
    async def test_returns_first(self):
        team = object()
        db = _DB([_Res(first=team)])
        assert await D.get_user_team(db, _user()) is team

    @pytest.mark.asyncio
    async def test_accepts_uuid(self):
        db = _DB([_Res(first=None)])
        assert await D.get_user_team(db, uuid.uuid4()) is None


class TestGetCurrentTeam:
    @pytest.mark.asyncio
    async def test_found(self):
        team = object()
        assert await D.get_current_team(uuid.uuid4(), _DB([_Res(scalar=team)])) is team

    @pytest.mark.asyncio
    async def test_missing_403(self):
        with pytest.raises(HTTPException) as ei:
            await D.get_current_team(uuid.uuid4(), _DB([_Res(scalar=None)]))
        assert ei.value.status_code == 403


class TestRole:
    @pytest.mark.asyncio
    async def test_role_returned(self):
        db = _DB([_Res(scalar=UserRole.ADMIN)])
        assert await D.get_user_role_in_team(_user(), uuid.uuid4(), db) == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_default_viewer(self):
        db = _DB([_Res(scalar=None)])
        assert await D.get_user_role_in_team(_user(), uuid.uuid4(), db) == UserRole.VIEWER

    @pytest.mark.asyncio
    async def test_require_role_pass(self):
        check = D.require_team_role(UserRole.EDITOR)
        user = _user()
        db = _DB([_Res(scalar=UserRole.OWNER)])
        assert await check(user, uuid.uuid4(), db) is user

    @pytest.mark.asyncio
    async def test_require_role_fail(self):
        check = D.require_team_role(UserRole.ADMIN)
        db = _DB([_Res(scalar=UserRole.VIEWER)])
        with pytest.raises(HTTPException) as ei:
            await check(_user(), uuid.uuid4(), db)
        assert ei.value.status_code == 403
        assert "admin" in ei.value.detail

    @pytest.mark.asyncio
    async def test_require_editor_factory(self):
        db = _DB([_Res(scalar=UserRole.EDITOR)])
        assert await D.require_team_editor(_user(), uuid.uuid4(), db) is not None


class TestCheckPlanFeature:
    def _settings(self, monkeypatch, admin="admin@x.co"):
        import app.core.config as cfg

        monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(SOCIAL_ADMIN_EMAIL=admin))

    def _quotas(self, monkeypatch, has):
        import app.core.quotas as q

        monkeypatch.setattr(q, "plan_has_feature", lambda tier, feat: has)

    @pytest.mark.asyncio
    async def test_admin_exempt(self, monkeypatch):
        self._settings(monkeypatch)
        self._quotas(monkeypatch, False)
        owner = _user(email="admin@x.co")
        db = _DB([_Res(scalar=owner.id), _Res(scalar="admin@x.co")])
        await D.check_plan_feature("f", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_owner_id_missing(self, monkeypatch):
        self._settings(monkeypatch)
        self._quotas(monkeypatch, True)
        db = _DB([_Res(scalar=None), _Res(scalar="pro")])
        await D.check_plan_feature("f", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_owner_email_mismatch(self, monkeypatch):
        self._settings(monkeypatch)
        self._quotas(monkeypatch, True)
        db = _DB([_Res(scalar=uuid.uuid4()), _Res(scalar="other@x.co"), _Res(scalar="pro")])
        await D.check_plan_feature("f", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_feature_missing_402(self, monkeypatch):
        self._settings(monkeypatch, admin=None)
        self._quotas(monkeypatch, False)
        db = _DB([_Res(scalar="free")])
        with pytest.raises(HTTPException) as ei:
            await D.check_plan_feature("analytics", uuid.uuid4(), db)
        assert ei.value.status_code == 402
        assert "analytics" in ei.value.detail

    @pytest.mark.asyncio
    async def test_tier_none_defaults_free(self, monkeypatch):
        self._settings(monkeypatch, admin=None)
        seen = {}
        import app.core.quotas as q

        monkeypatch.setattr(q, "plan_has_feature", lambda tier, feat: seen.setdefault("tier", tier) or True)
        db = _DB([_Res(scalar=None)])
        await D.check_plan_feature("f", uuid.uuid4(), db)
        assert seen["tier"] == "free"


class TestCheckQuota:
    def _env(self, monkeypatch, admin=None, limit=-1):
        import app.core.config as cfg
        import app.core.quotas as q

        monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(SOCIAL_ADMIN_EMAIL=admin))
        monkeypatch.setattr(q, "get_effective_limit", lambda tier, res: limit)

    @pytest.mark.asyncio
    async def test_admin_bypass(self, monkeypatch):
        self._env(monkeypatch, admin="a@x.co", limit=1)
        db = _DB([_Res(scalar=uuid.uuid4()), _Res(scalar="a@x.co")])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_admin_no_owner(self, monkeypatch):
        self._env(monkeypatch, admin="a@x.co", limit=-1)
        db = _DB([_Res(scalar=None), _Res(scalar="pro")])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_admin_email_mismatch(self, monkeypatch):
        self._env(monkeypatch, admin="a@x.co", limit=-1)
        db = _DB([_Res(scalar=uuid.uuid4()), _Res(scalar="b@x.co"), _Res(scalar="pro")])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_unlimited(self, monkeypatch):
        self._env(monkeypatch, limit=-1)
        db = _DB([_Res(scalar="free")])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_unknown_resource_returns(self, monkeypatch):
        self._env(monkeypatch, limit=5)
        db = _DB([_Res(scalar="free")])
        await D.check_quota("unknown_res", uuid.uuid4(), db)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("res", ["ai_calls_per_month", "posts_per_month", "social_accounts"])
    async def test_over_limit_429(self, monkeypatch, res):
        self._env(monkeypatch, limit=5)
        db = _DB([_Res(scalar="pro"), _Res(one=7)])
        with pytest.raises(HTTPException) as ei:
            await D.check_quota(res, uuid.uuid4(), db)
        assert ei.value.status_code == 429
        assert ei.value.headers["X-Quota-Resource"] == res
        assert ei.value.headers["X-Quota-Used"] == "7"

    @pytest.mark.asyncio
    async def test_under_limit_ok(self, monkeypatch):
        self._env(monkeypatch, limit=10)
        db = _DB([_Res(scalar="pro"), _Res(one=2)])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_warning_email_sent(self, monkeypatch):
        self._env(monkeypatch, limit=10)
        owner = _user(notification_preferences=None)
        db = _DB([_Res(scalar="pro"), _Res(one=9), _Res(one=0), _Res(scalar=owner)])
        import app.services.email_templates as et

        sent = Mock()
        monkeypatch.setattr(et, "send_quota_warning_email", sent)
        created = []
        import asyncio

        monkeypatch.setattr(asyncio, "create_task", lambda c: created.append(c))
        await D.check_quota("posts_per_month", uuid.uuid4(), db)
        sent.assert_called_once()
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_warning_already_sent(self, monkeypatch):
        self._env(monkeypatch, limit=10)
        db = _DB([_Res(scalar="pro"), _Res(one=9), _Res(one=1)])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_warning_owner_opted_out(self, monkeypatch):
        self._env(monkeypatch, limit=10)
        owner = _user(notification_preferences={"email_on_quota": False})
        db = _DB([_Res(scalar="pro"), _Res(one=9), _Res(one=0), _Res(scalar=owner)])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_warning_no_owner(self, monkeypatch):
        self._env(monkeypatch, limit=10)
        db = _DB([_Res(scalar="pro"), _Res(one=9), _Res(one=0), _Res(scalar=None)])
        await D.check_quota("posts_per_month", uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_warning_exception_nonfatal(self, monkeypatch):
        self._env(monkeypatch, limit=10)

        class _BoomDB:
            def __init__(self):
                self.n = 0

            async def execute(self, *a, **kw):
                self.n += 1
                if self.n == 1:
                    return _Res(scalar="pro")
                if self.n == 2:
                    return _Res(one=9)
                raise RuntimeError("db exploded")

        await D.check_quota("posts_per_month", uuid.uuid4(), _BoomDB())
