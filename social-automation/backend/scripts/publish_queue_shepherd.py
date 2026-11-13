#!/usr/bin/env python3
"""publish_queue_shepherd.py — keep stuck publishes moving.

Built 2026-11-13: the MS-outage campaign sat pending for hours because
process_publish_queue runs kept dying mid-flight to host flaps, leaving
rows locked 'processing' until the 25-min stale reclaim — and each new
run re-locked the whole batch. This shepherd closes the loop:

  1. Finds publish_queue rows stuck 'processing' longer than
     --stale-min (default 25, matches _STALE_LOCK_MINUTES) whose lock
     owner is dead (no active celery task) -> resets them to pending.
  2. Triggers process_publish_queue when pending rows exist and no
     queue task is active.
  3. Reports post_targets still pending > --pending-min (default 60)
     so a human/agent sees channels that never land (dead tokens etc).

Run inside social-worker-publishing (has app + DB env):
    docker compose exec -T social-worker-publishing \
        python /app/../scripts/publish_queue_shepherd.py
or from repo root with DATABASE_URL/REDIS env present.

Read-mostly; the only writes are queue status resets that the built-in
reclaim would do anyway, plus a celery task dispatch.
"""

import asyncio
import os
import subprocess
import sys
from urllib.parse import urlparse

import asyncpg

STALE_MIN = 25
PENDING_MIN = 60


def dsn():
    url = os.environ.get("DATABASE_URL") or os.environ.get("SOCIAL_DATABASE_URL") or ""
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    if not url:
        u = urlparse("postgresql://social-postgres:5432/social_automation")
        url = f"postgresql://{u.hostname}:{u.port}/{u.path.lstrip('/')}"
    return url


def celery(*args):
    subprocess.run(["celery", "-A", "app.worker.celery_app", *args], check=False, capture_output=True, text=True, timeout=120)


async def _run(stale_min, pend_min):
    conn = await asyncpg.connect(dsn())
    try:
        status = await conn.execute(
            """
            UPDATE publish_queue SET status='pending', locked_at=NULL,
                   locked_by=NULL
            WHERE status='processing'
              AND locked_at < now() - make_interval(mins => $1)""",
            stale_min,
        )
        pending = await conn.fetchval("SELECT count(*) FROM publish_queue WHERE status='pending'")
        aged = await conn.fetch(
            """
            SELECT a.platform, count(*)
            FROM post_targets pt JOIN social_accounts a
              ON a.id = pt.social_account_id
            WHERE pt.status='pending'
              AND coalesce(pt.updated_at, pt.published_at) < now() - make_interval(mins => $1)
            GROUP BY 1 ORDER BY 2 DESC""",
            pend_min,
        )
    finally:
        await conn.close()
    reclaimed = int(status.split()[-1]) if status else 0
    return reclaimed, pending, aged


def main():
    stale_min = int(sys.argv[sys.argv.index("--stale-min") + 1] if "--stale-min" in sys.argv else STALE_MIN)
    pend_min = int(sys.argv[sys.argv.index("--pending-min") + 1] if "--pending-min" in sys.argv else PENDING_MIN)
    reclaimed, pending, aged = asyncio.run(_run(stale_min, pend_min))
    dispatched = False
    if pending:
        celery("call", "app.worker.tasks.publishing.process_publish_queue")
        dispatched = True
    print(f"reclaimed_stale={reclaimed} pending_rows={pending} dispatched={dispatched}")
    if aged:
        print(f"targets pending >{pend_min}min (investigate):")
        for r in aged:
            print(f"  {r['platform']}: {r['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
