#!/usr/bin/env python3
"""engagement_sweep.py — run the full data-driven engagement pass.

Triggers every platform ingestor + the dispatcher through Celery, then
prints the candidate/action ledger. This is the "engage with other
accounts per strategy" one-shot: ingest -> score -> queue -> dispatch,
all inside the existing worker pipeline (caps, jitter, human gates,
sentiment kill-switches all still apply).

Usage:
  python3 engagement_sweep.py            # fire all ingests + dispatch, print ledger
  python3 engagement_sweep.py --wait 600 # same, then poll ledger for 10 min
  python3 engagement_sweep.py --ingest-only   # skip the dispatch trigger

Runs on the host; tasks execute inside social-worker-default. Requires
the stack up (docker + social-postgres + social-worker-default).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

WORKER = "social-worker-default"
PG = ["docker", "exec", "social-postgres", "psql", "-U", "social_user",
      "-d", "social_automation", "-tA", "-c"]

INGEST_TASKS = [
    "app.worker.tasks.engagement_ingest.ingest_x_feed",
    "app.worker.tasks.engagement_ingest_li.ingest_linkedin_feed",
    "app.worker.tasks.engagement_ingest_ig.ingest_ig_feed",
    "app.worker.tasks.engagement_ingest_tt.ingest_tiktok_tags",
    "app.worker.tasks.engagement_ingest_fb.ingest_fb_groups",
]
DISPATCH_TASK = "app.worker.tasks.engagement.dispatch_engagements"


def _send(task: str) -> str:
    code = (
        "from app.worker.celery_app import celery_app;"
        f"print(celery_app.send_task('{task}').id)"
    )
    out = subprocess.run(
        ["docker", "exec", WORKER, "python", "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr.strip()[:120]


def _psql(sql: str) -> str:
    out = subprocess.run(PG + [sql], capture_output=True, text=True, timeout=30)
    return out.stdout.strip() or out.stderr.strip()[:200]


def ledger() -> str:
    cands = _psql(
        "SELECT platform, count(*), count(*) FILTER (WHERE NOT scored) "
        "FROM engagement_candidates GROUP BY 1 ORDER BY 1;"
    )
    acts = _psql(
        "SELECT platform, action, status, count(*) FROM engagement_actions "
        "GROUP BY 1,2,3 ORDER BY 1,2,3;"
    )
    return f"candidates:\n{cands or '(none)'}\nactions:\n{acts or '(none)'}"


def sidecar_health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9225/health", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0, help="poll ledger N seconds")
    ap.add_argument("--ingest-only", action="store_true")
    args = ap.parse_args()

    li = sidecar_health()
    print(f"linkedin sidecar: {li}")
    if li.get("rate_limited"):
        print("sidecar rate-limited — LI ingest will skip cleanly")

    for t in INGEST_TASKS:
        print(f"ingest {t.split('.')[-1]}: {_send(t)}")
    if not args.ingest_only:
        print(f"dispatch: {_send(DISPATCH_TASK)}")

    if args.wait:
        print(f"polling ledger for {args.wait}s …")
        time.sleep(args.wait)
    print("\n" + ledger())
    return 0


if __name__ == "__main__":
    sys.exit(main())
