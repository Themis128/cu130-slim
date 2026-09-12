import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.content import Post, PostStatus
from app.models.user import Team, User
from app.services.slack_digest import build_daily_digest


async def main() -> None:
    async with async_session_maker() as db:
        user = User(email="digest-debug2@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        team = Team(name="Digest Debug2", owner_id=user.id)
        db.add(team)
        await db.flush()
        now = datetime.now(UTC)
        post = Post(
            team_id=team.id,
            user_id=user.id,
            status=PostStatus.FAILED,
            content_text="hello world",
            failure_reason="upstream timeout",
            failed_at=now,
            updated_at=now,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        print("post id", post.id)
        print("post.updated_at", repr(post.updated_at), type(post.updated_at))
        print("post.status", repr(post.status), type(post.status))
        print("team.id", team.id)
        since_24h = datetime.now(UTC) - timedelta(hours=24)
        rows = (
            await db.execute(
                select(Post).where(
                    Post.team_id == team.id,
                    Post.status == PostStatus.FAILED,
                    Post.updated_at >= since_24h,
                )
            )
        ).scalars().all()
        print("direct query count", len(rows), [(p.id, p.failure_reason, p.updated_at) for p in rows])
        report = await build_daily_digest(db, team=team, days=1)
        print("issues", [(i.severity, i.title, i.detail) for i in report.issues])
        print("overview", report.overview)
        await db.delete(post)
        await db.delete(team)
        await db.delete(user)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
