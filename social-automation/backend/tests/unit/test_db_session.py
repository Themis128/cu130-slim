"""Unit tests for app/db/session.py — get_db lifecycle + init_db admin seeding.

The AsyncSession is faked; the seeding/sync logic (admin password re-hash,
team bootstrap, enterprise upgrade) runs for real against stub results.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

import app.db.session as dbs


class _ScalarResult:
    def __init__(self, val=None):
        self._val = val

    def scalar_one_or_none(self):
        return self._val

    def scalars(self):
        return self

    def all(self):
        return self._val if isinstance(self._val, list) else ([] if self._val is None else [self._val])


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.closed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return self.results.pop(0) if self.results else _ScalarResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def close(self):
        self.closed += 1


@pytest.fixture
def patch_session(monkeypatch):
    holder = {}

    def _maker():
        return holder["session"]

    monkeypatch.setattr(dbs, "async_session_maker", _maker)
    return holder


@pytest.fixture
def admin_settings(monkeypatch):
    s = MagicMock()
    s.SOCIAL_ADMIN_EMAIL = "admin@cloudless.gr"
    s.SOCIAL_ADMIN_PASSWORD = "pw"
    s.SOCIAL_ADMIN_NAME = "Boss"
    s.APP_TIMEZONE = "Europe/Athens"
    s.DATABASE_URL = "x"
    s.DEBUG = False
    monkeypatch.setattr(dbs, "get_settings", lambda: s)
    return s


@pytest.mark.asyncio
async def test_get_db_yields_and_closes(patch_session):
    sess = _Session([])
    patch_session["session"] = sess
    gen = dbs.get_db()
    got = await gen.__anext__()
    assert got is sess
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert sess.closed == 1


@pytest.mark.asyncio
async def test_init_db_no_admin_configured(monkeypatch):
    s = MagicMock()
    s.SOCIAL_ADMIN_EMAIL = ""
    s.SOCIAL_ADMIN_PASSWORD = ""
    monkeypatch.setattr(dbs, "get_settings", lambda: s)
    monkeypatch.setattr(dbs, "async_session_maker", lambda: pytest.fail("session must not open"))
    await dbs.init_db()  # returns silently


@pytest.mark.asyncio
async def test_init_db_seeds_new_admin_and_team(patch_session, admin_settings, monkeypatch):
    from app.models.user import TeamMember

    monkeypatch.setattr(dbs, "hash_password", lambda p: f"hash:{p}")
    monkeypatch.setattr(dbs, "verify_password", lambda p, h: True)

    sess = _Session(
        [
            _ScalarResult(None),  # no existing admin
            _ScalarResult(None),  # no membership
            _ScalarResult([]),  # owned teams list
        ]
    )
    patch_session["session"] = sess

    await dbs.init_db()
    added_types = {type(o).__name__ for o in sess.added}
    assert "User" in added_types and "Team" in added_types
    assert any(isinstance(o, TeamMember) for o in sess.added)
    assert sess.commits >= 1


@pytest.mark.asyncio
async def test_init_db_resyncs_stale_admin(patch_session, admin_settings, monkeypatch):
    from app.models.user import Team

    admin = MagicMock()
    admin.id = uuid.uuid4()
    admin.name = "Old Name"
    admin.password_hash = "stale"
    admin.timezone = "UTC"
    admin.email = "admin@cloudless.gr"

    team = MagicMock(spec=Team)
    team.plan_tier = "starter"
    team.name = "T"

    monkeypatch.setattr(dbs, "hash_password", lambda p: f"hash:{p}")
    monkeypatch.setattr(dbs, "verify_password", lambda p, h: False)

    sess = _Session(
        [
            _ScalarResult(admin),  # existing admin
            _ScalarResult(object()),  # membership exists
            _ScalarResult([team]),  # owned teams
        ]
    )
    patch_session["session"] = sess

    await dbs.init_db()
    assert admin.password_hash == "hash:pw"  # re-hashed
    assert admin.name == "Boss"  # name resynced
    assert admin.timezone == "Europe/Athens"
    assert team.plan_tier == "enterprise"  # upgraded


@pytest.mark.asyncio
async def test_init_db_integrity_error_refetch(patch_session, admin_settings, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    admin = MagicMock()
    admin.id = uuid.uuid4()
    admin.name = "Boss"
    admin.timezone = "Europe/Athens"
    admin.password_hash = "hash:pw"

    monkeypatch.setattr(dbs, "hash_password", lambda p: f"hash:{p}")
    monkeypatch.setattr(dbs, "verify_password", lambda p, h: True)

    class _FlushingFailSession(_Session):
        async def flush(self):
            raise IntegrityError("stmt", {}, Exception("dup"))

    sess = _FlushingFailSession(
        [
            _ScalarResult(None),  # no admin initially
            _ScalarResult(admin),  # refetch after IntegrityError
            _ScalarResult(object()),  # membership exists
            _ScalarResult([]),  # owned teams
        ]
    )
    patch_session["session"] = sess
    await dbs.init_db()
    assert sess.rollbacks >= 1


@pytest.mark.asyncio
async def test_init_db_unresolvable_admin_returns(patch_session, admin_settings, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    monkeypatch.setattr(dbs, "hash_password", lambda p: "h")

    class _FlushingFailSession(_Session):
        async def flush(self):
            raise IntegrityError("stmt", {}, Exception("dup"))

    sess = _FlushingFailSession(
        [
            _ScalarResult(None),  # no admin
            _ScalarResult(None),  # refetch still None → bail
        ]
    )
    patch_session["session"] = sess
    await dbs.init_db()
    assert sess.rollbacks >= 1
