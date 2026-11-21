#!/usr/bin/env python3
"""repoint_publish_target.py — move a post_targets/publish_queue row to a
different social_accounts row (same platform), or add a new target.

Use cases proven 2026-11-22:
  - IG publish failed on fb_onboarding row (#10) → repoint to the healthy
    business_login row so the worker publishes via graph.instagram.com.
  - Add the personal FB profile as an extra target for an already-published
    post (post_targets + publish_queue insert, all NOT NULL fields set).

Runs INSIDE social-api:  docker exec -i social-api python - < this file
Or on host with DATABASE_URL reachable.

Examples:
  # repoint IG target of post X from old account to business_login row
  repoint_publish_target.py repoint --post <uuid> --from <uuid> --to <uuid>
  # add personal FB target
  repoint_publish_target.py add --post <uuid> --account <uuid>
  # show targets+queue for a post
  repoint_publish_target.py show --post <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import text

from app.db.session import async_session_maker


async def show(db, pid: str):
    r = await db.execute(text("""
        select sa.platform, sa.username, sa.account_type, pt.status,
               pt.platform_url, pq.status, pq.attempts
        from post_targets pt
        join social_accounts sa on sa.id = pt.social_account_id
        left join publish_queue pq
          on pq.post_id = pt.post_id and pq.social_account_id = pt.social_account_id
        where pt.post_id = :pid order by sa.platform, sa.username"""), {"pid": pid})
    for row in r:
        print(row)


async def repoint(db, pid: str, old: str, new: str):
    r1 = await db.execute(text("""
        update publish_queue set social_account_id = :new, status = 'pending',
               attempts = 0, locked_at = null, locked_by = null, scheduled_at = now()
        where post_id = :pid and social_account_id = :old
          and status in ('processing','failed','pending')"""),
        {"new": new, "pid": pid, "old": old})
    r2 = await db.execute(text("""
        update post_targets set social_account_id = :new, status = 'pending',
               error_message = null, error_class = null,
               published_at = null, platform_post_id = null, platform_url = null
        where post_id = :pid and social_account_id = :old"""),
        {"new": new, "pid": pid, "old": old})
    await db.commit()
    print(f"queue rows: {r1.rowcount}, target rows: {r2.rowcount}")


async def add(db, pid: str, acct: str):
    await db.execute(text("""
        insert into post_targets (post_id, social_account_id, status, updated_at)
        values (:p, :a, 'pending', now())
        on conflict (post_id, social_account_id) do nothing"""),
        {"p": pid, "a": acct})
    await db.execute(text("""
        insert into publish_queue
            (id, post_id, social_account_id, scheduled_at, priority,
             attempts, max_attempts, status, created_at)
        values (:id, :p, :a, now(), 10, 0, 3, 'pending', now())"""),
        {"id": uuid.uuid4(), "p": pid, "a": acct})
    await db.commit()
    print("target+queue added")


async def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "repoint", "add"):
        s = sub.add_parser(name)
        s.add_argument("--post", required=True)
        if name == "repoint":
            s.add_argument("--from", dest="old", required=True)
            s.add_argument("--to", dest="new", required=True)
        if name == "add":
            s.add_argument("--account", required=True)
    args = ap.parse_args()
    async with async_session_maker() as db:
        if args.cmd == "show":
            await show(db, args.post)
        elif args.cmd == "repoint":
            await repoint(db, args.post, args.old, args.new)
        else:
            await add(db, args.post, args.account)


if __name__ == "__main__":
    asyncio.run(main())
