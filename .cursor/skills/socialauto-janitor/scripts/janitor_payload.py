#!/usr/bin/env python3
"""janitor_payload.py — executed INSIDE the social-api container.

Usage: docker exec -i -w /app social-api python - <command> [args]

Commands:
  media-orphans [--pattern REGEX] [--older-than-hours H] [--apply]
  queue-status
  queue-requeue <post_id_prefix> [--delay-minutes M] [--apply]
  fixtures [--apply]
  accounts
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from sqlalchemy import func, select, cast, String, update
from app.db.session import async_session_maker
from app.models.content import Post, MediaAsset, PostTarget
from app.models.queue import PublishQueue
from app.models.social_account import SocialAccount


def _args():
    return sys.argv[1:]


def _flag(args, name):
    return name in args


def _opt(args, name, default):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


async def media_orphans(args):
    pattern = _opt(args, "--pattern", "")
    hours = float(_opt(args, "--older-than-hours", "24"))
    apply_ = _flag(args, "--apply")
    rx = re.compile(pattern) if pattern else None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with async_session_maker() as db:
        referenced = set(
            (await db.execute(
                select(func.unnest(Post.media_ids)).where(Post.media_ids.isnot(None))
            )).scalars().all()
        )
        rows = (await db.execute(
            select(MediaAsset).where(
                MediaAsset.is_archived.is_(False),
                MediaAsset.created_at < cutoff,
            )
        )).scalars().all()
        orphans = []
        for a in rows:
            if a.id in referenced:
                continue
            if rx and not rx.search(a.filename or ""):
                continue
            orphans.append(a)
        print(f"unreferenced assets older than {hours}h matching /{pattern or '.*'}/: {len(orphans)}")
        for a in orphans[:50]:
            print(f"  {a.id}  {a.filename}  {a.created_at}  {(a.public_url or '')[:70]}")
        if len(orphans) > 50:
            print(f"  ... and {len(orphans) - 50} more")
        if apply_ and orphans:
            ids = [a.id for a in orphans]
            await db.execute(
                update(MediaAsset).where(MediaAsset.id.in_(ids)).values(is_archived=True)
            )
            await db.commit()
            print(f"ARCHIVED {len(ids)} assets")
        elif orphans:
            print("dry-run — pass --apply to archive")


async def queue_status(_args):
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(PublishQueue.status, func.count())
            .group_by(PublishQueue.status)
        )).all()
        for st, n in rows:
            print(f"  {st}: {n}")
        stuck = (await db.execute(
            select(PublishQueue, SocialAccount.platform)
            .join(SocialAccount, SocialAccount.id == PublishQueue.social_account_id)
            .where(PublishQueue.status.in_(["pending", "processing", "failed"]))
            .order_by(PublishQueue.scheduled_at)
        )).all()
        print("open rows:")
        for q, plat in stuck[:40]:
            print(f"  {str(q.id)[:8]} post={str(q.post_id)[:8]} {plat} "
                  f"{q.status} att={q.attempts} sched={q.scheduled_at}")


async def queue_requeue(args):
    prefix = args[0] if args else ""
    delay = float(_opt(args, "--delay-minutes", "60"))
    apply_ = _flag(args, "--apply")
    async with async_session_maker() as db:
        posts = (await db.execute(
            select(Post).where(cast(Post.id, String).like(f"{prefix}%"))
        )).scalars().all()
        if not posts:
            print("no post matching prefix"); return
        p = posts[0]
        rows = (await db.execute(
            select(PublishQueue)
            .where(PublishQueue.post_id == p.id, PublishQueue.status == "failed")
            .order_by(PublishQueue.scheduled_at.desc())
        )).scalars().all()
        if not rows:
            print(f"post {p.id} has no failed queue rows"); return
        latest = rows[0]
        when = datetime.now(timezone.utc) + timedelta(minutes=delay)
        print(f"post={p.id} queue_row={latest.id} -> pending at {when.isoformat()}")
        if apply_:
            await db.execute(
                update(PublishQueue)
                .where(PublishQueue.id == latest.id)
                .values(status="pending", scheduled_at=when, attempts=0,
                        locked_at=None, locked_by=None)
            )
            await db.commit()
            print("requeued")
        else:
            print("dry-run — pass --apply")


_FIXTURE_RE = re.compile(r"(e2e|fixture|seeded|test post|dummy|lorem)", re.I)


async def fixtures(args):
    apply_ = _flag(args, "--apply")
    async with async_session_maker() as db:
        posts = (await db.execute(
            select(Post).where(Post.status.in_(["draft", "scheduled", "failed", "review"]))
        )).scalars().all()
        hits = []
        for p in posts:
            text = p.content_text or ""
            if not _FIXTURE_RE.search(text):
                continue
            n_targets = (await db.execute(
                select(func.count()).select_from(PostTarget)
                .where(PostTarget.post_id == p.id)
            )).scalar_one()
            if n_targets == 0:
                hits.append(p)
        print(f"targetless fixture-like posts: {len(hits)}")
        for p in hits:
            print(f"  {p.id} status={p.status} sched={p.scheduled_at} "
                  f"head={(p.content_text or '')[:50]!r}")
        if apply_ and hits:
            ids = [p.id for p in hits]
            await db.execute(
                update(Post).where(Post.id.in_(ids)).values(status="archived")
            )
            await db.commit()
            print(f"ARCHIVED {len(ids)} posts")
        elif hits:
            print("dry-run — pass --apply to archive")


async def accounts(_args):
    async with async_session_maker() as db:
        rows = (await db.execute(select(SocialAccount))).scalars().all()
        bad = [a for a in rows if (a.status or "") not in ("active", "connected", "ok")]
        print(f"accounts: {len(rows)} total, {len(bad)} non-active")
        for a in bad:
            print(f"  {a.platform:12s} {a.username or a.account_id} status={a.status} id={a.id}")


async def main():
    args = _args()
    cmd = args[0] if args else "help"
    rest = args[1:]
    if cmd == "media-orphans":
        await media_orphans(rest)
    elif cmd == "queue-status":
        await queue_status(rest)
    elif cmd == "queue-requeue":
        await queue_requeue(rest)
    elif cmd == "fixtures":
        await fixtures(rest)
    elif cmd == "accounts":
        await accounts(rest)
    else:
        print(__doc__)


asyncio.run(main())
