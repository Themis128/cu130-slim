"""Integration tests for the team management API.

Follows the pattern in tests/integration/test_auth.py.  Each test function
gets a fresh database (the ``engine`` fixture drops & recreates all tables).
"""

import uuid

import pytest

USER_A = {"email": "usera@example.com", "password": "TestPass123!", "name": "User A"}
USER_B = {"email": "userb@example.com", "password": "TestPass123!", "name": "User B"}


async def _register(client, user):
    resp = await client.post("/api/v1/auth/register", json=user)
    assert resp.status_code == 201
    return resp.json()


async def _login(client, user):
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": user["email"], "password": user["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_teams(client):
    """userA can list their teams (default team created at registration)."""
    await _register(client, USER_A)
    token = await _login(client, USER_A)

    resp = await client.get("/api/v1/teams", headers=_auth(token))
    assert resp.status_code == 200
    teams = resp.json()
    assert len(teams) == 1
    assert teams[0]["role"] == "owner"
    assert teams[0]["member_count"] == 1


@pytest.mark.asyncio
async def test_create_second_team(client):
    """userA can create a second team and becomes OWNER."""
    await _register(client, USER_A)
    token = await _login(client, USER_A)

    resp = await client.post("/api/v1/teams", json={"name": "Second Team"}, headers=_auth(token))
    assert resp.status_code == 201
    team = resp.json()
    assert team["name"] == "Second Team"
    assert team["role"] == "owner"
    assert team["member_count"] == 1

    # Verify it appears in the list.
    resp = await client.get("/api/v1/teams", headers=_auth(token))
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_invite_existing_user(client):
    """userA can invite userB by email; userB sees the team."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    # Get userA's default team.
    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    # Invite userB by email.
    resp = await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["invited"] is True

    # userB lists teams and sees the invited team.
    resp = await client.get("/api/v1/teams", headers=_auth(token_b))
    assert resp.status_code == 200
    b_teams = resp.json()
    team_ids = [t["id"] for t in b_teams]
    assert team_id in team_ids
    invited = [t for t in b_teams if t["id"] == team_id][0]
    assert invited["role"] == "editor"


@pytest.mark.asyncio
async def test_editor_cannot_delete_team(client):
    """userB (as EDITOR) cannot delete the team."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )

    resp = await client.delete(f"/api/v1/teams/{team_id}", headers=_auth(token_b))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_change_role(client):
    """userA (OWNER) can change userB's role to ADMIN."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )

    # Get userB's user_id from the team detail.
    detail = (await client.get(f"/api/v1/teams/{team_id}", headers=_auth(token_a))).json()
    user_b_id = [m for m in detail["members"] if m["email"] == USER_B["email"]][0]["user_id"]

    resp = await client.patch(
        f"/api/v1/teams/{team_id}/members/{user_b_id}",
        json={"role": "admin"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_can_update_team_name(client):
    """userB (now ADMIN) can update the team name."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )

    detail = (await client.get(f"/api/v1/teams/{team_id}", headers=_auth(token_a))).json()
    user_b_id = [m for m in detail["members"] if m["email"] == USER_B["email"]][0]["user_id"]

    await client.patch(
        f"/api/v1/teams/{team_id}/members/{user_b_id}",
        json={"role": "admin"},
        headers=_auth(token_a),
    )

    resp = await client.patch(
        f"/api/v1/teams/{team_id}",
        json={"name": "Renamed by Admin"},
        headers=_auth(token_b),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed by Admin"


@pytest.mark.asyncio
async def test_owner_can_remove_member(client):
    """userA can remove userB from the team."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )

    detail = (await client.get(f"/api/v1/teams/{team_id}", headers=_auth(token_a))).json()
    user_b_id = [m for m in detail["members"] if m["email"] == USER_B["email"]][0]["user_id"]

    resp = await client.delete(
        f"/api/v1/teams/{team_id}/members/{user_b_id}",
        headers=_auth(token_a),
    )
    assert resp.status_code == 204

    # userB no longer sees the team.
    b_teams = (await client.get("/api/v1/teams", headers=_auth(token_b))).json()
    assert team_id not in [t["id"] for t in b_teams]


@pytest.mark.asyncio
async def test_owner_can_delete_solo_team(client):
    """OWNER can delete a team when they are the only member."""
    await _register(client, USER_A)
    token_a = await _login(client, USER_A)

    # Create a second team (only userA is a member).
    resp = await client.post(
        "/api/v1/teams",
        json={"name": "Solo Team"},
        headers=_auth(token_a),
    )
    team_id = resp.json()["id"]

    resp = await client.delete(f"/api/v1/teams/{team_id}", headers=_auth(token_a))
    assert resp.status_code == 204

    # Verify it's gone.
    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    assert team_id not in [t["id"] for t in teams]


@pytest.mark.asyncio
async def test_owner_cannot_delete_team_with_members(client):
    """OWNER cannot delete a team that still has other members."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "editor"},
        headers=_auth(token_a),
    )

    resp = await client.delete(f"/api/v1/teams/{team_id}", headers=_auth(token_a))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_switch_team_issues_new_token(client):
    """switch-team issues a new token with the team_id claim."""
    await _register(client, USER_A)
    token_a = await _login(client, USER_A)

    # Create a second team.
    resp = await client.post(
        "/api/v1/teams",
        json={"name": "Second Team"},
        headers=_auth(token_a),
    )
    second_team_id = resp.json()["id"]

    # Switch to the second team.
    resp = await client.post(
        "/api/v1/auth/switch-team",
        json={"team_id": second_team_id},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]
    assert new_token != token_a

    # The new token should still authenticate.
    resp = await client.get("/api/v1/auth/me", headers=_auth(new_token))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_switch_team_non_member_forbidden(client):
    """A user cannot switch to a team they are not a member of."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(await _login(client, USER_A)))).json()
    team_a_id = teams[0]["id"]

    resp = await client.post(
        "/api/v1/auth/switch-team",
        json={"team_id": team_a_id},
        headers=_auth(token_b),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_isolation_between_users(client):
    """userB cannot access userA's posts (team isolation)."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    # userA creates a post.
    resp = await client.post(
        "/api/v1/content/posts",
        json={"content_text": "User A secret post"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 201
    post_id = resp.json()["id"]

    # userB lists posts — should not see userA's post.
    resp = await client.get("/api/v1/content/posts", headers=_auth(token_b))
    assert resp.status_code == 200
    posts = resp.json()["posts"]
    post_ids = [p["id"] for p in posts]
    assert post_id not in post_ids


@pytest.mark.asyncio
async def test_get_team_detail(client):
    """A member can get team details with member list."""
    await _register(client, USER_A)
    token_a = await _login(client, USER_A)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    resp = await client.get(f"/api/v1/teams/{team_id}", headers=_auth(token_a))
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == team_id
    assert len(detail["members"]) == 1
    assert detail["members"][0]["role"] == "owner"


@pytest.mark.asyncio
async def test_non_member_cannot_get_team(client):
    """A non-member cannot view team details."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    resp = await client.get(f"/api/v1/teams/{team_id}", headers=_auth(token_b))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invite_nonexistent_user_returns_token(client):
    """Inviting a non-existent user returns an invite token."""
    await _register(client, USER_A)
    token_a = await _login(client, USER_A)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    resp = await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": "nonexistent@example.com", "role": "editor"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["invited"] is False
    assert data["token"] is not None


@pytest.mark.asyncio
async def test_viewer_cannot_update_team_name(client):
    """A VIEWER cannot update the team name."""
    await _register(client, USER_A)
    await _register(client, USER_B)
    token_a = await _login(client, USER_A)
    token_b = await _login(client, USER_B)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]

    await client.post(
        f"/api/v1/teams/{team_id}/invite",
        json={"email": USER_B["email"], "role": "viewer"},
        headers=_auth(token_a),
    )

    resp = await client.patch(
        f"/api/v1/teams/{team_id}",
        json={"name": "Hacked"},
        headers=_auth(token_b),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_owner_cannot_remove_self_if_only_owner(client):
    """An OWNER cannot remove themselves if they are the only owner."""
    await _register(client, USER_A)
    token_a = await _login(client, USER_A)

    teams = (await client.get("/api/v1/teams", headers=_auth(token_a))).json()
    team_id = teams[0]["id"]
    user_a_id = (await client.get("/api/v1/auth/me", headers=_auth(token_a))).json()["id"]

    resp = await client.delete(
        f"/api/v1/teams/{team_id}/members/{user_a_id}",
        headers=_auth(token_a),
    )
    assert resp.status_code == 400
