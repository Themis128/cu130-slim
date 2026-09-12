import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.content import Post, PostStatus
from app.models.user import Team, User


TEST_USER = {"email": "ci-analytics@example.com", "password": "TestPass123!", "name": "CI Analytics"}


@pytest.mark.asyncio
async def test_overview_counts_upcoming_scheduled_posts(client, db):
    # Register + login to get a real team-scoped JWT
    await client.post("/api/v1/auth/register", json=TEST_USER)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = login.json()["access_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = uuid.UUID(me.json()["id"])

    # Resolve the default team created on register
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    team = (await db.execute(select(Team).where(Team.owner_id == user.id))).scalar_one()

    now = datetime.now(UTC)

    # Create a scheduled post that was created long ago (older than overview window),
    # but is still scheduled in the future (should count as scheduled_posts).
    old_created = now - timedelta(days=60)
    future_scheduled = now + timedelta(days=2)
    db.add(
        Post(
            team_id=team.id,
            user_id=user.id,
            status=PostStatus.SCHEDULED,
            content_text="Scheduled from the past",
            created_at=old_created,
            scheduled_at=future_scheduled,
        )
    )
    await db.commit()

    overview = await client.get(
        "/api/v1/analytics/overview",
        params={"days": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert overview.status_code == 200
    data = overview.json()

    assert data["scheduled_posts"] == 1

