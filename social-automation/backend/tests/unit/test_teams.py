"""Unit tests for team management schemas and role hierarchy logic.

These tests do not require a database — they validate the Pydantic schemas
and the role-hierarchy helper functions used by the teams API.
"""

import uuid

import pytest

from app.api.teams import (
    ChangeRoleRequest,
    InviteRequest,
    MemberResponse,
    TeamCreate,
    TeamDetailResponse,
    TeamResponse,
    TeamUpdate,
    _ROLE_LEVEL,
    _role_at_least,
)
from app.models.user import UserRole


# ── Role hierarchy ────────────────────────────────────────────────────────────


class TestRoleHierarchy:
    """OWNER > ADMIN > EDITOR > VIEWER."""

    def test_level_ordering(self):
        assert _ROLE_LEVEL[UserRole.OWNER] > _ROLE_LEVEL[UserRole.ADMIN]
        assert _ROLE_LEVEL[UserRole.ADMIN] > _ROLE_LEVEL[UserRole.EDITOR]
        assert _ROLE_LEVEL[UserRole.EDITOR] > _ROLE_LEVEL[UserRole.VIEWER]

    def test_owner_is_highest(self):
        assert _ROLE_LEVEL[UserRole.OWNER] == 3

    def test_viewer_is_lowest(self):
        assert _ROLE_LEVEL[UserRole.VIEWER] == 0

    @pytest.mark.parametrize(
        "role,minimum,expected",
        [
            (UserRole.OWNER, UserRole.OWNER, True),
            (UserRole.OWNER, UserRole.ADMIN, True),
            (UserRole.OWNER, UserRole.EDITOR, True),
            (UserRole.OWNER, UserRole.VIEWER, True),
            (UserRole.ADMIN, UserRole.OWNER, False),
            (UserRole.ADMIN, UserRole.ADMIN, True),
            (UserRole.ADMIN, UserRole.EDITOR, True),
            (UserRole.EDITOR, UserRole.ADMIN, False),
            (UserRole.EDITOR, UserRole.EDITOR, True),
            (UserRole.EDITOR, UserRole.VIEWER, True),
            (UserRole.VIEWER, UserRole.EDITOR, False),
            (UserRole.VIEWER, UserRole.VIEWER, True),
        ],
    )
    def test_role_at_least(self, role, minimum, expected):
        assert _role_at_least(role, minimum) is expected

    def test_viewer_cannot_do_admin_tasks(self):
        assert not _role_at_least(UserRole.VIEWER, UserRole.ADMIN)

    def test_editor_cannot_do_owner_tasks(self):
        assert not _role_at_least(UserRole.EDITOR, UserRole.OWNER)

    def test_admin_cannot_do_owner_tasks(self):
        assert not _role_at_least(UserRole.ADMIN, UserRole.OWNER)


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class TestTeamSchemas:
    def test_team_create(self):
        team = TeamCreate(name="My Team")
        assert team.name == "My Team"

    def test_team_update(self):
        team = TeamUpdate(name="Updated")
        assert team.name == "Updated"

    def test_team_response(self):
        owner_id = uuid.uuid4()
        team_id = uuid.uuid4()
        resp = TeamResponse(
            id=team_id,
            name="Team A",
            owner_id=owner_id,
            member_count=3,
            role=UserRole.OWNER,
        )
        assert resp.member_count == 3
        assert resp.role == UserRole.OWNER

    def test_team_detail_response(self):
        team_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        member = MemberResponse(
            user_id=owner_id,
            email="owner@example.com",
            name="Owner",
            role=UserRole.OWNER,
        )
        detail = TeamDetailResponse(
            id=team_id,
            name="Team A",
            owner_id=owner_id,
            members=[member],
        )
        assert len(detail.members) == 1
        assert detail.members[0].role == UserRole.OWNER

    def test_member_response_serialization(self):
        user_id = uuid.uuid4()
        member = MemberResponse(
            user_id=user_id,
            email="viewer@example.com",
            name="Viewer",
            role=UserRole.VIEWER,
        )
        data = member.model_dump()
        assert data["role"] == UserRole.VIEWER
        assert data["email"] == "viewer@example.com"


class TestInviteSchema:
    def test_invite_request_default_role(self):
        req = InviteRequest(email="user@example.com")
        assert req.role == UserRole.EDITOR

    def test_invite_request_custom_role(self):
        req = InviteRequest(email="user@example.com", role=UserRole.ADMIN)
        assert req.role == UserRole.ADMIN

    def test_invite_request_invalid_email(self):
        with pytest.raises(Exception):
            InviteRequest(email="not-an-email")


class TestChangeRoleSchema:
    def test_change_role_request(self):
        req = ChangeRoleRequest(role=UserRole.ADMIN)
        assert req.role == UserRole.ADMIN

    def test_change_role_to_owner(self):
        req = ChangeRoleRequest(role=UserRole.OWNER)
        assert req.role == UserRole.OWNER


# ── UserRole enum ─────────────────────────────────────────────────────────────


class TestUserRoleEnum:
    def test_str_values(self):
        assert UserRole.OWNER.value == "owner"
        assert UserRole.ADMIN.value == "admin"
        assert UserRole.EDITOR.value == "editor"
        assert UserRole.VIEWER.value == "viewer"

    def test_all_roles_present(self):
        roles = {UserRole.OWNER, UserRole.ADMIN, UserRole.EDITOR, UserRole.VIEWER}
        assert len(roles) == 4
