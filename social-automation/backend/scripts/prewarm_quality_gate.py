"""Pre-compute the publish quality gate for posts stuck behind DMR latency.

The go-live gate (``_enforce_publish_quality`` in
``app/worker/tasks/publishing.py``) costs several DMR calls per post.
Inside a Celery task the ~900s soft limit kills runs mid-gate under
``gpu:dmr_lock`` contention, so the result cached in
``post.platform_specific["quality_gate"]`` never lands and every retry
re-pays the full pipeline. This script runs the same gate with no task
time limit and commits the cache per post — afterwards the queue
publishes fast (the gate short-circuits on the content hash).

Idempotent: posts whose cache already matches their content hash are
skipped, so re-running after a container restart resumes where it left
off.

Run inside a worker container (needs app imports + DATABASE_URL):

    docker exec -d social-worker-publishing \
        python /app/scripts/prewarm_quality_gate.py            # auto-discover
    docker exec -d social-worker-publishing \
        python /app/scripts/prewarm_quality_gate.py <uuid>...  # explicit posts

Auto-discovery picks posts that have a publish_queue row in
pending/processing AND no ``quality_gate`` cache on the post — i.e.
exactly the rows a queue run would stall on.
"""
import asyncio
import hashlib
import sys
import traceback
import uuid

from sqlalchemy import select

from app.models.content import Post, PostTarget
from app.models.queue import PublishQueue, QueueStatus
from app.models.social_account import SocialAccount
from app.worker._async import task_session
from app.worker.tasks.publishing import _enforce_publish_quality


async def _discover_post_ids(db) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            select(PublishQueue.post_id)
            .where(PublishQueue.status.in_([QueueStatus.PENDING, QueueStatus.PROCESSING]))
            .distinct()
        )
    ).scalars().all()
    ids = []
    for pid in rows:
        post = (
            await db.execute(select(Post).where(Post.id == pid))
        ).scalar_one_or_none()
        if post is None:
            continue
        text = getattr(post, "content_text", None) or ""
        if not text.strip():
            continue
        cached = (post.platform_specific or {}).get("quality_gate") or {}
        if cached.get("content_hash") == hashlib.sha1(text.encode()).hexdigest():
            continue  # gate already computed for this exact copy
        ids.append(pid)
    return ids


async def _prewarm_one(db, pid: uuid.UUID) -> None:
    post = (
        await db.execute(select(Post).where(Post.id == pid))
    ).scalar_one_or_none()
    if post is None:
        print(f"{pid}: POST NOT FOUND", flush=True)
        return
    tgt = (
        await db.execute(select(PostTarget).where(PostTarget.post_id == post.id))
    ).scalars().first()
    if tgt is None:
        print(f"{pid}: NO TARGET", flush=True)
        return
    acct = (
        await db.execute(
            select(SocialAccount).where(SocialAccount.id == tgt.social_account_id)
        )
    ).scalar_one_or_none()
    if acct is None:
        print(f"{pid}: NO ACCOUNT", flush=True)
        return
    gate = await _enforce_publish_quality(post, acct, db)
    await db.commit()
    print(
        f"{pid} {acct.platform}: passed={getattr(gate, 'passed', None)} "
        f"overall={getattr(gate, 'overall', None)} "
        f"reason={getattr(gate, 'reason', None)}",
        flush=True,
    )


async def main() -> None:
    explicit = [a for a in sys.argv[1:] if not a.startswith("-")]
    async with task_session() as db:
        if explicit:
            ids = [uuid.UUID(a) for a in explicit]
        else:
            ids = await _discover_post_ids(db)
            print(f"auto-discovered {len(ids)} post(s) needing gate prewarm", flush=True)
        for pid in ids:
            try:
                await _prewarm_one(db, pid)
            except Exception:
                await db.rollback()
                print(f"{pid}: ERROR\n{traceback.format_exc()}", flush=True)
    print("PREWARM DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
