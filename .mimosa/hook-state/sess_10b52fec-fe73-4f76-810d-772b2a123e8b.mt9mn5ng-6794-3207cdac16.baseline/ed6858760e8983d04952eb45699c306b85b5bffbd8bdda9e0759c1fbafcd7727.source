import pytest

TEST_USER = {"email": "ci-test@example.com", "password": "TestPass123!", "name": "CI Test"}


@pytest.mark.asyncio
async def test_register_new_user(client):
    resp = await client.post("/api/v1/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == TEST_USER["email"]
    assert data["name"] == TEST_USER["name"]
    assert "id" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    await client.post("/api/v1/auth/register", json=TEST_USER)
    resp = await client.post("/api/v1/auth/register", json=TEST_USER)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_login_valid_credentials(client):
    await client.post("/api/v1/auth/register", json=TEST_USER)
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/v1/auth/register", json=TEST_USER)
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": "wrongpass"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me_with_valid_token(client):
    await client.post("/api/v1/auth/register", json=TEST_USER)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = login.json()["access_token"]

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == TEST_USER["email"]


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_refresh(client):
    await client.post("/api/v1/auth/register", json=TEST_USER)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
