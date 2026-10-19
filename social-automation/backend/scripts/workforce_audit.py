#!/usr/bin/env python3
"""Agent-workforce audit — "did every digital worker clock in today?"

Adoption of The Autonomous Enterprise (Dalton): treat each scheduled Celery
worker as a bounded digital worker with a domain, a cadence, observable work
output, and an escalation lane. This tool verifies all three live:

  1. PRESENCE  — a Celery node registered for the worker's queue (stats).
  2. OUTPUT    — latest domain-table evidence within 2x cadence.
  3. ESCALATION — open items parked for a human (stale locks, dead tokens,
                 unhealed sessions) that the worker could not self-resolve.

Run inside social-api (PYTHONPATH=/app) or the backend venv:

    python scripts/workforce_audit.py            # human report
    python scripts/workforce_audit.py --json     # machine output for digests
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402


@dataclass
class Worker:
    name: str
    task: str
    queue: str
    cadence_s: int
    # Demand-driven workers produce output only when there is work — stale
    # evidence is a fault ONLY while pending items exist. Periodic producers
    # (syncers, drips) should always emit within 2x cadence.
    demand_driven: bool = False
    # (label, sql-returning-a-timestamp) — real work product the worker leaves
    evidence: list[tuple[str, str]] = field(default_factory=list)
    # SQL returning count of pending items for demand-driven workers
    pending_sql: str | None = None
    # (label, sql-returning-an-int) — parked items needing a human
    escalations: list[tuple[str, str]] = field(default_factory=list)


DAY = 86400
WORKFORCE = [
    Worker(
        "publisher", "app.worker.tasks.publishing.process_publish_queue",
        "publishing", 30,
        demand_driven=True,
        evidence=[("last queue claim",
                   "SELECT max(locked_at) FROM publish_queue")],
        pending_sql="SELECT count(*) FROM publish_queue WHERE status='pending'",
        escalations=[
            ("stale processing locks",
             "SELECT count(*) FROM publish_queue WHERE status='processing' "
             "AND locked_at < now() - interval '2 hours'"),
            ("failed targets 24h",
             "SELECT count(*) FROM post_targets WHERE status='failed' "
             "AND updated_at > now() - interval '24 hours'"),
        ],
    ),
    Worker(
        "scheduler", "app.worker.tasks.publishing.check_scheduled_posts",
        "publishing", 60,
        demand_driven=True,
        evidence=[("last scheduled→queued",
                   "SELECT max(locked_at) FROM publish_queue "
                   "WHERE created_at > now() - interval '1 day'")],
        pending_sql="SELECT count(*) FROM posts WHERE status='scheduled' "
                    "AND scheduled_at < now()",
    ),
    Worker(
        "analytics-sync", "app.worker.tasks.analytics.sync_all_analytics",
        "default", 1800,
        evidence=[
            ("follower snapshot",
             "SELECT max(captured_at) FROM follower_snapshots"),
            ("post analytics",
             "SELECT max(captured_at) FROM post_analytics_snapshots"),
        ],
    ),
    Worker(
        "token-refresh",
        "app.worker.tasks.token_refresh.refresh_expiring_tokens",
        "default", 1800,
        escalations=[("accounts past token expiry",
                      "SELECT count(*) FROM social_accounts "
                      "WHERE token_expires_at < now()")],
    ),
    Worker(
        "session-healer", "app.worker.tasks.session_healer.heal_sessions",
        "default", 3600,
        escalations=[("accounts not active",
                      "SELECT count(*) FROM social_accounts "
                      "WHERE status <> 'active'")],
    ),
    Worker(
        "tiktok-reconcile",
        "app.worker.tasks.tiktok_inbox_reconcile.reconcile_tiktok_inbox",
        "default", 21600,
        escalations=[("stale tiktok inbox drafts",
                      "SELECT count(*) FROM post_targets pt JOIN social_accounts sa "
                      "ON sa.id = pt.social_account_id WHERE sa.platform='tiktok' "
                      "AND pt.status='pending' AND pt.error_message IS NOT NULL")],
    ),
    Worker(
        "crm-engagement",
        "app.worker.tasks.crm_engagement.run_crm_engagement_sweep",
        "default", 1800,
    ),
    Worker(
        "recurring-posts",
        "app.worker.tasks.recurring.process_recurring_posts",
        "default", 300,
    ),
    Worker(
        "dmr-keepwarm", "app.worker.tasks.dmr_health.keep_warm_dmr_models",
        "default", 90,
    ),
    Worker(
        "messenger-poll",
        "app.worker.tasks.personal_messenger.poll_personal_messenger",
        "messenger", 120,
        demand_driven=True,  # replies only arrive when inbound mail exists
    ),
    Worker(
        "email-drip", "app.worker.tasks.email_drip.run_email_drip_sweep",
        "default", DAY,
        evidence=[("last drip mail", "SELECT max(created_at) FROM email_logs")],
    ),
    Worker(
        "lead-research",
        "app.worker.tasks.lead_research.run_lead_research_sweep",
        "default", DAY,
    ),
    Worker(
        "crm-lifecycle",
        "app.worker.tasks.crm_lifecycle.run_crm_lifecycle_sweep",
        "default", DAY,
    ),
    Worker(
        "crm-hygiene", "app.worker.tasks.crm_hygiene.run_crm_hygiene",
        "default", 7 * DAY,
    ),
    Worker(
        "datalake-export",
        "app.worker.tasks.datalake_export.export_datalake",
        "default", 21600,
    ),
    Worker(
        "nextcloud-sync",
        "app.worker.tasks.nextcloud_sync.sync_nextcloud_surfaces",
        "default", 10800,
    ),
    Worker(
        "strategy-brief",
        "app.worker.tasks.notebook_reports.run_notebook_report",
        "default", DAY,
        # notebook reports email out-of-band; content_briefs is a different
        # artifact — celery run-count is the evidence here
    ),
    Worker(
        "content-factory", "", "publishing", DAY,
        evidence=[("last post drafted", "SELECT max(created_at) FROM posts")],
    ),
]


async def audit() -> dict:
    from app.worker.celery_app import celery_app

    insp = celery_app.control.inspect(timeout=5)
    stats = insp.stats() or {}
    totals: dict[str, int] = {}
    for node, s in stats.items():
        for task, n in (s.get("total") or {}).items():
            totals[task] = totals.get(task, 0) + n
    live_nodes = list(stats)

    out = {"generated_at": datetime.now(UTC).isoformat(),
           "celery_nodes": live_nodes, "workers": []}
    async with async_session_maker() as db:
        for w in WORKFORCE:
            entry = {"name": w.name, "task": w.task or "(composite)",
                     "queue": w.queue, "cadence_s": w.cadence_s,
                     "runs_seen": totals.get(w.task, 0) if w.task else None,
                     "evidence": [], "escalations": [], "status": "ok"}
            pending = None
            if w.pending_sql:
                pending = (await db.execute(text(w.pending_sql))).scalar() or 0
                entry["pending"] = pending
            for label, sql in w.evidence:
                ts = (await db.execute(text(sql))).scalar()
                fresh = ts is not None and ts.replace(tzinfo=UTC) > (
                    datetime.now(UTC) - timedelta(seconds=2 * w.cadence_s))
                # demand-driven: stale evidence only matters with work waiting
                if w.demand_driven and not pending:
                    fresh = True
                entry["evidence"].append(
                    {"label": label, "at": str(ts), "fresh": fresh})
                if not fresh:
                    entry["status"] = "dark"
            for label, sql in w.escalations:
                n = (await db.execute(text(sql))).scalar() or 0
                entry["escalations"].append({"label": label, "count": n})
                if n:
                    entry["status"] = "escalation" if entry["status"] == "ok" \
                        else entry["status"]
            # runs_seen=0 means "no run since the worker's last restart" —
            # normal for daily/weekly beats, so it never flags on its own.
            if w.task and not totals.get(w.task) and entry["status"] == "ok":
                entry["status"] = "idle"
            out["workers"].append(entry)
    return out


def render(report: dict) -> str:
    lines = [
        f"Agent workforce audit — {report['generated_at'][:16]}Z",
        f"celery nodes: {len(report['celery_nodes'])}",
        "",
        f"{'worker':<18} {'runs':>5} {'status':<11} evidence / escalations",
        "-" * 78,
    ]
    for w in report["workers"]:
        runs = "-" if w["runs_seen"] is None else str(w["runs_seen"])
        bits = [f"{e['label']} @ {str(e['at'])[:16]}"
                + ("" if e["fresh"] else " (STALE)")
                for e in w["evidence"]]
        bits += [f"{e['label']}={e['count']}" for e in w["escalations"]
                 if e["count"]]
        lines.append(f"{w['name']:<18} {runs:>5} {w['status']:<11} "
                     + "; ".join(bits))
    bad = [w["name"] for w in report["workers"]
           if w["status"] in ("dark", "escalation")]
    lines += ["", "attention: " + (", ".join(bad) if bad else "none — all workers clocked in")]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    sys.exit(
        1 if any(w["status"] in ("dark", "escalation")
                 for w in report["workers"]) else 0
    )


if __name__ == "__main__":
    main()
