"""Endpoint-level tests for app/api/teams.py.

test_teams.py covers the Pydantic schemas and _ROLE_LEVEL ordering only —
this file exercises the actual route functions: CRUD, the invite JWT flow,
member management, and the role-permission matrix.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.teams import (
    AcceptInviteRequest,
    AddMemberRequest,
    ChangeRoleRequest,
    InviteRequest,
    TeamCreate,
    TeamUpdate,
    _assert_membership,
    _assert_role,
    _count_members,
    _get_membership,
    _get_team_or_404,
    accept_invite,
    add_member,
    change_member_role,
    create_team,
    delete_team,
    get_team,
    invite_member,
    list_teams,
    remove_member,
    update_team,
)
from app.models.user import UserRole

# ── Fakes ─────────────────────────────────────────────────────────────


class _Res:
    """Single-result stub supporting the SQLAlchemy result API surface used here."""

    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalar_one(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else []


class _DB:
    """Queue-based fake session — each execute() pops the next queued result."""

    def __init__(self, results=()):
        self._q = list(results)
        self.added = []
        self.deleted = []
        self.committed = 0
        self.refreshed = []

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)
        # Real flush assigns ids; emulate so create_team can read team.id.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)


def _team(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        name=kw.pop("name", "Cloudless"),
        owner_id=kw.pop("owner_id", uuid.uuid4()),
        **kw,
    )


def _member(team_id, user_id, role=UserRole.EDITOR):
    return SimpleNamespace(team_id=team_id, user_id=user_id, role=role)


def _user(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        email=kw.pop("email", "u@example.com"),
        name=kw.pop("name", "User"),
        notification_preferences=kw.pop("notification_preferences", {}),
        **kw,
    )


# ── helpers ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_or_404_missing():
    with pytest.raises(HTTPException) as e:
        await _get_team_or_404(_DB([None]), uuid.uuid4())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_get_team_found():
    t = _team()
    assert await _get_team_or_404(_DB([t]), t.id) is t


@pytest.mark.asyncio
async def test_get_membership_none_and_found():
    assert await _get_membership(_DB([None]), uuid.uuid4(), uuid.uuid4()) is None
    m = _member(uuid.uuid4(), uuid.uuid4())
    assert await _get_membership(_DB([m]), m.team_id, m.user_id) is m


@pytest.mark.asyncio
async def test_count_members():
    assert await _count_members(_DB([7]), uuid.uuid4()) == 7


@pytest.mark.asyncio
async def test_assert_membership_403():
    with pytest.raises(HTTPException) as e:
        await _assert_membership(_DB([None]), uuid.uuid4(), uuid.uuid4())
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_assert_role_insufficient():
    m = _member(uuid.uuid4(), uuid.uuid4(), UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await _assert_role(_DB([m]), m.team_id, m.user_id, UserRole.ADMIN)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_assert_role_sufficient():
    m = _member(uuid.uuid4(), uuid.uuid4(), UserRole.ADMIN)
    out = await _assert_role(_DB([m]), m.team_id, m.user_id, UserRole.ADMIN)
    assert out is m


# ── list_teams ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_teams_empty():
    out = await list_teams(_user(), _DB([[]]))
    assert out == []


@pytest.mark.asyncio
async def test_list_teams_returns_roles_and_counts():
    u = _user()
    t1, t2 = _team(), _team(name="Other")
    db = _DB([[(t1, UserRole.OWNER), (t2, UserRole.EDITOR)], 1, 5])
    out = await list_teams(u, db)
    assert len(out) == 2
    assert out[0].role == UserRole.OWNER and out[0].member_count == 1
    assert out[1].role == UserRole.EDITOR and out[1].member_count == 5


# ── create_team ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_team_owner_membership():
    u = _user()
    db = _DB()
    out = await create_team(TeamCreate(name="New Team"), u, db)
    assert out.name == "New Team"
    assert out.owner_id == u.id
    assert out.role == UserRole.OWNER
    assert out.member_count == 1
    assert len(db.added) == 2  # team + membership
    assert db.committed == 1


# ── get_team ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_404():
    with pytest.raises(HTTPException) as e:
        await get_team(uuid.uuid4(), _user(), _DB([None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_get_team_non_member_403():
    t = _team()
    with pytest.raises(HTTPException) as e:
        await get_team(t.id, _user(), _DB([t, None]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_get_team_lists_members():
    t = _team()
    u1, u2 = _user(email="a@x.com"), _user(email="b@x.com")
    m1 = _member(t.id, u1.id, UserRole.OWNER)
    m2 = _member(t.id, u2.id, UserRole.VIEWER)
    db = _DB([t, m1, [(m1, u1), (m2, u2)]])
    out = await get_team(t.id, u1, db)
    assert out.id == t.id
    assert [m.email for m in out.members] == ["a@x.com", "b@x.com"]
    assert out.members[1].role == UserRole.VIEWER


# ── update_team ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_team_requires_admin():
    t = _team()
    m = _member(t.id, _user().id)  # role defaults to... build fresh
    u = _user()
    m = _member(t.id, u.id, UserRole.EDITOR)
    with pytest.raises(HTTPException) as e:
        await update_team(t.id, TeamUpdate(name="X"), u, _DB([t, m]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_update_team_admin_ok():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.ADMIN)
    db = _DB([t, m, [(m, u)]])
    out = await update_team(t.id, TeamUpdate(name="Renamed"), u, db)
    assert out.name == "Renamed"
    assert db.committed == 1


# ── delete_team ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_team_requires_owner():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        await delete_team(t.id, u, _DB([t, m]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_team_400_other_members():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.OWNER)
    with pytest.raises(HTTPException) as e:
        await delete_team(t.id, u, _DB([t, m, 3]))  # count=3
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_team_solo_owner_ok():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.OWNER)
    db = _DB([t, m, 1])
    await delete_team(t.id, u, db)
    assert db.deleted == [t]
    assert db.committed == 1


# ── invite_member ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invite_requires_admin():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await invite_member(t.id, InviteRequest(email="x@x.com"), u, _DB([t, m]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_existing_member_updates_role():
    t = _team()
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    target = _user(email="member@x.com")
    existing = _member(t.id, target.id, UserRole.VIEWER)
    db = _DB([t, admin, target, existing])
    out = await invite_member(t.id, InviteRequest(email="member@x.com", role=UserRole.ADMIN), u, db)
    assert out.invited is True
    assert "already a member" in out.message
    assert existing.role == UserRole.ADMIN  # role updated in place
    assert db.committed == 1


@pytest.mark.asyncio
async def test_invite_existing_member_same_role_no_commit():
    t = _team()
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    target = _user(email="member@x.com")
    existing = _member(t.id, target.id, UserRole.EDITOR)
    db = _DB([t, admin, target, existing])
    out = await invite_member(t.id, InviteRequest(email="member@x.com", role=UserRole.EDITOR), u, db)
    assert out.invited is True
    assert db.committed == 0


@pytest.mark.asyncio
async def test_invite_existing_user_new_member(monkeypatch):
    t = _team(name="MyTeam")
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    target = _user(email="new@x.com", notification_preferences={"email_on_invite": False})
    db = _DB([t, admin, target, None])  # user exists, no existing membership
    out = await invite_member(t.id, InviteRequest(email="new@x.com"), u, db)
    assert out.invited is True
    assert "added to the team" in out.message
    assert len(db.added) == 1  # the new TeamMember


@pytest.mark.asyncio
async def test_invite_unknown_user_issues_token():
    t = _team()
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    db = _DB([t, admin, None])  # no user with that email
    out = await invite_member(t.id, InviteRequest(email="stranger@x.com"), u, db)
    assert out.invited is False
    assert out.token  # signed invite JWT returned
    assert "token generated" in out.message


# ── add_member ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_member_requires_admin():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.EDITOR)
    with pytest.raises(HTTPException) as e:
        await add_member(t.id, uuid.uuid4(), AddMemberRequest(), u, _DB([t, m]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_add_member_404_unknown_user():
    t = _team()
    u = _user()
    m = _member(t.id, u.id, UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        await add_member(t.id, uuid.uuid4(), AddMemberRequest(), u, _DB([t, m, None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_add_member_400_already_member():
    t = _team()
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    target = _user()
    existing = _member(t.id, target.id, UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await add_member(t.id, target.id, AddMemberRequest(), u, _DB([t, admin, target, existing]))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_add_member_success():
    t = _team()
    u = _user()
    admin = _member(t.id, u.id, UserRole.ADMIN)
    target = _user(email="t@x.com", name="Target")
    db = _DB([t, admin, target, None])
    out = await add_member(t.id, target.id, AddMemberRequest(role=UserRole.EDITOR), u, db)
    assert out.email == "t@x.com"
    assert out.role == UserRole.EDITOR
    assert len(db.added) == 1


# ── change_member_role — permission matrix ────────────────────────────


@pytest.mark.asyncio
async def test_change_role_target_404():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.OWNER)
    with pytest.raises(HTTPException) as e:
        await change_member_role(t.id, uuid.uuid4(), ChangeRoleRequest(role=UserRole.EDITOR), u,
                                 _DB([t, actor, None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_change_role_editor_forbidden():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.EDITOR)
    target = _member(t.id, uuid.uuid4(), UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.ADMIN), u,
                                 _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_change_role_admin_cannot_touch_owner():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, uuid.uuid4(), UserRole.OWNER)
    with pytest.raises(HTTPException) as e:
        await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.EDITOR), u,
                                 _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_change_role_admin_cannot_touch_admin():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, uuid.uuid4(), UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.EDITOR), u,
                                 _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_change_role_admin_cannot_promote_owner():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, uuid.uuid4(), UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.OWNER), u,
                                 _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_change_role_admin_to_editor_ok():
    t = _team()
    u = _user()
    target_user = _user(email="t@x.com")
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, target_user.id, UserRole.VIEWER)
    db = _DB([t, actor, target, target_user])
    out = await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.EDITOR), u, db)
    assert out.role == UserRole.EDITOR
    assert target.role == UserRole.EDITOR


@pytest.mark.asyncio
async def test_change_role_owner_transfers_ownership():
    t = _team()
    u = _user()
    target_user = _user()
    actor = _member(t.id, u.id, UserRole.OWNER)
    target = _member(t.id, target_user.id, UserRole.ADMIN)
    db = _DB([t, actor, target, target_user])
    out = await change_member_role(t.id, target.user_id, ChangeRoleRequest(role=UserRole.OWNER), u, db)
    assert out.role == UserRole.OWNER
    assert t.owner_id == target_user.id  # ownership transferred


# ── remove_member — permission matrix ────────────────────────────────


@pytest.mark.asyncio
async def test_remove_member_target_404():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        await remove_member(t.id, uuid.uuid4(), u, _DB([t, actor, None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_editor_forbidden():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.EDITOR)
    target = _member(t.id, uuid.uuid4(), UserRole.VIEWER)
    with pytest.raises(HTTPException) as e:
        await remove_member(t.id, target.user_id, u, _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_admin_cannot_remove_admin():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, uuid.uuid4(), UserRole.ADMIN)
    with pytest.raises(HTTPException) as e:
        await remove_member(t.id, target.user_id, u, _DB([t, actor, target]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_admin_removes_viewer():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.ADMIN)
    target = _member(t.id, uuid.uuid4(), UserRole.VIEWER)
    db = _DB([t, actor, target])
    await remove_member(t.id, target.user_id, u, db)
    assert db.deleted == [target]


@pytest.mark.asyncio
async def test_remove_member_owner_self_removal_blocked():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.OWNER)
    db = _DB([t, actor, actor, 1])  # owner_count=1
    with pytest.raises(HTTPException) as e:
        await remove_member(t.id, u.id, u, db)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_remove_member_owner_self_ok_when_other_owner():
    t = _team()
    u = _user()
    actor = _member(t.id, u.id, UserRole.OWNER)
    db = _DB([t, actor, actor, 2])  # another owner exists
    await remove_member(t.id, u.id, u, db)
    assert db.deleted == [actor]


# ── accept_invite ─────────────────────────────────────────────────────


def _invite_token_payload(email="invitee@x.com", team_id=None, role="editor"):
    return {
        "invite_email": email,
        "invite_team_id": str(team_id or uuid.uuid4()),
        "invite_role": role,
    }


@pytest.mark.asyncio
async def test_accept_invite_bad_token_400(monkeypatch):
    monkeypatch.setattr("app.core.security.decode_token", lambda t: None)
    with pytest.raises(HTTPException) as e:
        await accept_invite(AcceptInviteRequest(token="junk"), _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_not_invite_token_400(monkeypatch):
    monkeypatch.setattr("app.core.security.decode_token", lambda t: {"sub": "x"})
    with pytest.raises(HTTPException) as e:
        await accept_invite(AcceptInviteRequest(token="t"), _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_wrong_email_403(monkeypatch):
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda t: _invite_token_payload(email="other@x.com"))
    with pytest.raises(HTTPException) as e:
        await accept_invite(AcceptInviteRequest(token="t"), _user(email="me@x.com"), _DB())
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_accept_invite_bad_team_uuid_400(monkeypatch):
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda t: {"invite_email": "me@x.com", "invite_team_id": "not-a-uuid"})
    with pytest.raises(HTTPException) as e:
        await accept_invite(AcceptInviteRequest(token="t"), _user(email="me@x.com"), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_adds_member(monkeypatch):
    t = _team()
    u = _user(email="invitee@x.com")
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda tok: _invite_token_payload(email=u.email, team_id=t.id, role="admin"))
    new_mem = _member(t.id, u.id, UserRole.ADMIN)
    db = _DB([t, None, 3, new_mem])  # team, no existing membership, count, final lookup
    out = await accept_invite(AcceptInviteRequest(token="tok"), u, db)
    assert len(db.added) == 1
    assert db.added[0].role == UserRole.ADMIN
    assert out.id == t.id


@pytest.mark.asyncio
async def test_accept_invite_existing_member_role_update(monkeypatch):
    t = _team()
    u = _user(email="invitee@x.com")
    existing = _member(t.id, u.id, UserRole.VIEWER)
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda tok: _invite_token_payload(email=u.email, team_id=t.id, role="editor"))
    db = _DB([t, existing, 2, existing])
    out = await accept_invite(AcceptInviteRequest(token="tok"), u, db)
    assert existing.role == UserRole.EDITOR
    assert out.member_count == 2


@pytest.mark.asyncio
async def test_accept_invite_invalid_role_defaults_editor(monkeypatch):
    t = _team()
    u = _user(email="invitee@x.com")
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda tok: _invite_token_payload(email=u.email, team_id=t.id, role="bogus"))
    mem = _member(t.id, u.id, UserRole.EDITOR)
    db = _DB([t, None, 1, mem])
    await accept_invite(AcceptInviteRequest(token="tok"), u, db)
    assert db.added[0].role == UserRole.EDITOR


@pytest.mark.asyncio
async def test_accept_invite_existing_member_bogus_role_defaults(monkeypatch):
    t = _team()
    u = _user(email="invitee@x.com")
    existing = _member(t.id, u.id, UserRole.VIEWER)
    monkeypatch.setattr("app.core.security.decode_token",
                        lambda tok: _invite_token_payload(email=u.email, team_id=t.id, role="bogus"))
    db = _DB([t, existing, 2, existing])
    await accept_invite(AcceptInviteRequest(token="tok"), u, db)
    assert existing.role == UserRole.EDITOR  # bogus role falls back to EDITOR
