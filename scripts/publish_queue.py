#!/usr/bin/env python3
"""Inspect and repair the SocialAuto publish queue.

Runs read/write SQL against social-postgres (DB: social_automation).

Usage:
    python3 scripts/publish_queue.py list [--status STATUS] [--limit N]
    python3 scripts/publish_queue.py targets <post_id>
    python3 scripts/publish_queue.py reset <queue_id>     # failed/stuck -> pending
    python3 scripts/publish_queue.py unstick            # reclaim stale 'processing' rows
    python3 scripts/publish_queue.py dupes <post_id>    # show per-target publish state

The queue claims rows atomically via SELECT ... FOR UPDATE SKIP LOCKED
(app/worker/tasks/publishing.py). Rows stuck in 'processing' are reclaimed
by the worker after STALE_LOCK_MINUTES; `unstick` forces it immediately.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = [
    "docker", "compose", "exec", "-T", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]


def sql(query: str) -> list[list[str]]:
    res = subprocess.run(
        [*PSQL, "-c", query], cwd=ROOT, capture_output=True, text=True
    )
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    return [r.split("\t") for r in res.stdout.strip().splitlines() if r.strip()]


def cmd_list(args) -> None:
    where = f"WHERE q.status = '{args.status}'" if args.status else ""
    rows = sql(
        f"""SELECT q.id, q.status, q.attempts, q.max_attempts, q.locked_by,
                   sa.platform, sa.username, left(coalesce(q.last_error,''),80),
                   q.scheduled_at::text
            FROM publish_queue q
            LEFT JOIN social_accounts sa ON sa.id = q.social_account_id
            {where}
            ORDER BY q.created_at DESC LIMIT {args.limit}"""
    )
    for r in rows:
        print("\t".join(r))


def cmd_targets(args) -> None:
    rows = sql(
        f"""SELECT sa.platform, sa.username, t.status, t.platform_post_id,
                   left(coalesce(t.platform_url,''),70),
                   left(coalesce(t.error_message,''),100)
            FROM post_targets t
            JOIN social_accounts sa ON sa.id = t.social_account_id
            WHERE t.post_id = '{args.post_id}' ORDER BY sa.platform"""
    )
    for r in rows:
        print("\t".join(r))


def cmd_reset(args) -> None:
    sql(
        f"""UPDATE publish_queue SET status='pending', attempts=0,
            locked_at=NULL, locked_by=NULL WHERE id='{args.queue_id}'"""
    )
    sql(
        f"""UPDATE post_targets SET status='pending', error_message=NULL
            WHERE post_id = (SELECT post_id FROM publish_queue WHERE id='{args.queue_id}')
              AND social_account_id = (SELECT social_account_id FROM publish_queue WHERE id='{args.queue_id}')"""
    )
    print(f"reset {args.queue_id} -> pending (target reset too)")


def cmd_unstick(args) -> None:
    mins = args.minutes
    rows = sql(
        f"""UPDATE publish_queue SET status='pending', locked_at=NULL, locked_by=NULL
            WHERE status='processing' AND locked_at < now() - interval '{mins} minutes'
            RETURNING id"""
    )
    print(f"unstuck {len(rows)} row(s)")
    for r in rows:
        print(" ", r[0])


def cmd_dupes(args) -> None:
    """Show queue + target rows for a post so duplicate external posts can be reconciled."""
    print("-- publish_queue --")
    for r in sql(
        f"""SELECT q.id, sa.platform, q.status, q.attempts, q.locked_by, q.created_at::text
            FROM publish_queue q LEFT JOIN social_accounts sa ON sa.id=q.social_account_id
            WHERE q.post_id='{args.post_id}' ORDER BY q.created_at"""
    ):
        print("\t".join(r))
    print("-- post_targets --")
    cmd_targets(args)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--limit", type=int, default=20); p.set_defaults(f=cmd_list)
    p = sub.add_parser("targets"); p.add_argument("post_id"); p.set_defaults(f=cmd_targets)
    p = sub.add_parser("reset"); p.add_argument("queue_id"); p.set_defaults(f=cmd_reset)
    p = sub.add_parser("unstick"); p.add_argument("--minutes", type=int, default=15); p.set_defaults(f=cmd_unstick)
    p = sub.add_parser("dupes"); p.add_argument("post_id"); p.set_defaults(f=cmd_dupes)

    args = ap.parse_args()
    args.f(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
