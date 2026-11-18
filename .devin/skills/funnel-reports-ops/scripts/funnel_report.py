#!/usr/bin/env python3
"""funnel_report.py — ops CLI for the SocialAuto funnel report suite.

Commands:
  status    — watcher table (which reports are due/missing) — no resend
  resend    — force the watcher resend pass for missing reports
  run KEY   — queue a funnel_reports task inside social-worker-default
  build KEY — run a report builder live inside social-api (no email)
  inbox     — queue an inbox_snapshot capture now
  verify    — IMAP check that today's expected report emails arrived

KEY ∈ lead_pulse | attribution | content_weekly | funnel_monthly
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WATCH = REPO / ".devin/skills/report-delivery-ops/scripts/report_delivery_watch.py"

TASKS = {
    "lead_pulse": "app.worker.tasks.funnel_reports.run_lead_pulse",
    "attribution": "app.worker.tasks.funnel_reports.run_attribution_digest",
    "content_weekly": "app.worker.tasks.funnel_reports.run_content_weekly",
    "funnel_monthly": "app.worker.tasks.funnel_reports.run_funnel_monthly",
}
BUILDERS = {
    "lead_pulse": "build_lead_pulse",
    "attribution": "build_attribution",
    "content_weekly": "build_content_weekly",
    "funnel_monthly": "build_funnel_monthly",
}


def _docker_exec(container: str, code: str) -> int:
    return subprocess.call(["docker", "exec", container, "python", "-c", code])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "resend", "run", "build", "inbox", "verify"])
    ap.add_argument("key", nargs="?", choices=sorted(TASKS))
    args = ap.parse_args()

    if args.cmd in ("status", "resend", "verify"):
        flag = {"status": "--status", "resend": "--resend", "verify": "--status"}[args.cmd]
        return subprocess.call([sys.executable, str(WATCH), flag])

    if args.cmd == "inbox":
        return _docker_exec(
            "social-worker-default",
            "from app.worker.celery_app import celery_app;"
            "print(celery_app.send_task("
            "'app.worker.tasks.inbox_snapshot.run_inbox_snapshot').id)")

    if not args.key:
        ap.error(f"{args.cmd} requires a key: {', '.join(sorted(TASKS))}")

    if args.cmd == "run":
        return _docker_exec(
            "social-worker-default",
            f"from app.worker.celery_app import celery_app;"
            f"print(celery_app.send_task({TASKS[args.key]!r}, kwargs={{'send': True}}).id)")

    # build — live run, prints the text report, sends nothing
    return _docker_exec(
        "social-api",
        "import asyncio\n"
        f"from app.services.funnel_reports import {BUILDERS[args.key]}\n"
        f"print(asyncio.run({BUILDERS[args.key]}()).to_text())")


if __name__ == "__main__":
    sys.exit(main())
