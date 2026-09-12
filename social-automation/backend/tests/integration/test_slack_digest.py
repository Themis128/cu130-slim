from datetime import UTC, datetime

import pytest

from app.models.content import Post, PostStatus
from app.models.user import Team, User
from app.services.slack_digest import build_daily_digest


@pytest.mark.asyncio
async def test_build_daily_digest_includes_failed_post_failure_reason(db):
    user = User(email="digest@example.com", password_hash="x")
    db.add(user)
    await db.flush()

    team = Team(name="Digest Team", owner_id=user.id)
    db.add(team)
    await db.flush()

    post = Post(
        team_id=team.id,
        user_id=user.id,
        status=PostStatus.FAILED,
        content_text="hello world",
        failure_reason="upstream timeout",
        failed_at=datetime.now(UTC),
    )
    db.add(post)
    await db.commit()

    report = await build_daily_digest(db, team=team, days=1)

    assert any(
        i.severity == "error" and "Post failed" in i.title and "upstream timeout" in i.detail
        for i in report.issues
    )

