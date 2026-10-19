#!/usr/bin/env python3
"""Agent-workforce audit — "did every digital worker clock in today?"

Adoption of The Autonomous Enterprise (Dalton): treat each scheduled Celery
worker as a bounded digital worker with a domain, a cadence, observable work
output, and an escalation lane. Each worker ALSO carries the Agentic BPM
governance contract (Vu et al., BPM 2025 — arXiv:2504.03693): an explicit
goal, hard guardrails, an autonomy tier, and a declared fallback — a worker
missing any of them reports `ungoverned`.

  1. PRESENCE   — a live consumer on the worker's queue (active_queues).
                  If inspect itself returns nothing, every worker is dark —
                  a clean report while all containers are down is a false pass.
  2. OUTPUT     — latest domain-table evidence within 2x cadence. Workers
                  with no verifiable artifact report `unverified`, never `ok`
                  (stats().total counts since worker start — it proves
                  presence, not recency).
  3. ESCALATION — open items parked for a human (stale locks, dead tokens,
                  unhealed sessions) that the worker could not self-resolve.
  4. GOVERNANCE — goal + guardrails + autonomy + fallback declared.

Run inside social-api (PYTHONPATH=/app) or the backend venv:

    python scripts/workforce_audit.py             # human report
    python scripts/workforce_audit.py --governance  # + governance contract
    python scripts/workforce_audit.py --json      # machine output for digests
    python scripts/workforce_audit.py --slack     # push attention list to Slack
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
    # ── ABPM governance fields (Vu et al., BPM 2025) ──
    # goal: the one business outcome this worker owns
    goal: str = ""
    # guardrails: hard boundaries it must never cross
    guardrails: tuple[str, ...] = ()
    # autonomy: "auto" (no review) | "review" (quality gate / approval) |
    # "supervised" (human must confirm each action)
    autonomy: str = "auto"
    # fallback: what happens when the worker fails (the paper's "safe
    # reversion" requirement)
    fallback: str = ""
    # Demand-driven workers produce output only when there is work — stale
    # evidence is a fault ONLY while pending items exist. Periodic producers
    # (syncers, drips) should always emit within 2x cadence.
    demand_driven: bool = False
    # (label, sql-returning-a-timestamp) — real work product the worker leaves
    evidence: list[tuple[str, str]] = field(default_factory=list)
    # SQL returning count of pending items for demand-driven workers
    pending_sql: str | None = None
    # (label, sql, rationale) — parked items needing a human; the rationale
    # is the manifesto's explanans: the worker states WHY the item needs a
    # principal, not just that it exists
    escalations: list[tuple[str, str, str]] = field(default_factory=list)
    # scheduled=False for composite/on-demand entries that no beat dispatches
    # — a quiet period must never darken them (they report 'unverified')
    scheduled: bool = True


# APM manifesto (Calvanese et al., arXiv:2603.18916): frames exist on agent,
# process, and organization levels — and org-level normative frames project
# onto every agent. These rules bind all workers regardless of contract.
GLOBAL_FRAME = (
    "never spend money — promotional credits only",
    "never publish without media; never republish duplicates",
    "bots disclose they are bots; deterministic handlers before the LLM",
    "real data only — no fabricated metrics in any output",
    "quarantine beats delete — no bulk data removal",
)


DAY = 86400
WORKFORCE = [
    Worker(
        "publisher",
        "app.worker.tasks.publishing.process_publish_queue",
        "publishing",
        30,
        goal="Deliver every queued post to its target platform",
        guardrails=(
            "never publish without media",
            "never republish a target already marked published",
            "skip duplicates (media phash + 30-day post dedup)",
        ),
        fallback="retry with backoff → mark target failed → Slack alert",
        demand_driven=True,
        evidence=[("last queue claim", "SELECT max(locked_at) FROM publish_queue")],
        # only claimable work counts as pending — future-dated rows are
        # not publisher work yet (process_publish_queue cannot claim them)
        pending_sql="SELECT count(*) FROM publish_queue WHERE status='pending' AND scheduled_at <= now()",
        escalations=[
            (
                "stale processing locks",
                "SELECT count(*) FROM publish_queue WHERE status='processing' AND locked_at < now() - interval '2 hours'",
                "claimed but never finished — the claimer crashed; requeue or inspect the worker",
            ),
            (
                "failed targets 24h",
                "SELECT count(*) FROM post_targets WHERE status='failed' AND updated_at > now() - interval '24 hours'",
                "retries exhausted — platform error needs a human decision",
            ),
        ],
    ),
    Worker(
        "scheduler",
        "app.worker.tasks.publishing.check_scheduled_posts",
        "publishing",
        60,
        goal="Move due scheduled posts into the publish queue on time",
        guardrails=("never queue a post twice",),
        fallback="post stays scheduled; next sweep retries",
        demand_driven=True,
        # check_scheduled_posts CREATES queue rows — its artifact is
        # created_at, not locked_at (which is the publisher's claim)
        evidence=[("last scheduled→queued", "SELECT max(created_at) FROM publish_queue")],
        pending_sql="SELECT count(*) FROM posts WHERE status='scheduled' AND scheduled_at < now()",
    ),
    Worker(
        "analytics-sync",
        "app.worker.tasks.analytics.sync_all_analytics",
        "default",
        1800,
        goal="Keep follower + post-metric snapshots current for digests",
        guardrails=("write only plausible follower counts (_plausible_follower_count guard)",),
        fallback="per-platform error recorded; next sweep retries",
        evidence=[
            # per-account staleness: the min over accounts of each account's
            # latest snapshot — a fresh account can no longer hide stale ones
            (
                "stalest follower account",
                "SELECT min(last) FROM ("
                "  SELECT social_account_id, max(captured_at) AS last"
                "  FROM follower_snapshots GROUP BY social_account_id) s"
                "  JOIN social_accounts sa ON sa.id = s.social_account_id"
                "  WHERE sa.status = 'active'",
            ),
            (
                "stalest post-analytics account",
                "SELECT min(last) FROM ("
                "  SELECT sa.id AS acct, max(pas.captured_at) AS last"
                "  FROM post_analytics_snapshots pas"
                "  JOIN posts p ON p.id = pas.post_id"
                "  JOIN post_targets pt ON pt.post_id = p.id"
                "  JOIN social_accounts sa ON sa.id = pt.social_account_id"
                "  GROUP BY sa.id) s WHERE true",
            ),
        ],
        escalations=[
            (
                "active accounts w/ stale follower sync",
                "SELECT count(*) FROM ("
                "  SELECT sa.id FROM social_accounts sa"
                "  LEFT JOIN LATERAL ("
                "    SELECT max(captured_at) AS last"
                "    FROM follower_snapshots fs"
                "    WHERE fs.social_account_id = sa.id) s ON true"
                "  WHERE sa.status = 'active'"
                "  AND sa.platform IN"
                "      ('facebook','instagram','linkedin','x',"
                "       'tiktok','threads')"
                "  AND (s.last IS NULL"
                "       OR s.last < now() - interval '1 hour')) t",
                "per-account sync is failing — check the platform's follower endpoint (see follower-analytics-guard)",
            )
        ],
    ),
    Worker(
        "token-refresh",
        "app.worker.tasks.token_refresh.refresh_expiring_tokens",
        "default",
        1800,
        goal="Refresh OAuth tokens before they expire",
        guardrails=(
            "never shorten a stored expiry",
            "never touch non-expired tokens",
        ),
        fallback="expired token → session-healer escalation",
        escalations=[
            (
                "accounts past token expiry",
                "SELECT count(*) FROM social_accounts WHERE token_expires_at < now()",
                "refresh failed or impossible — reconnect the account in the UI",
            )
        ],
    ),
    Worker(
        "session-healer",
        "app.worker.tasks.session_healer.heal_sessions",
        "default",
        3600,
        goal="Keep every social account connected and logged in",
        guardrails=(
            "never store a session that failed the probe",
            "credential login only as last resort",
        ),
        fallback="account stays non-active → Slack alert for human login",
        escalations=[
            (
                "accounts not active",
                "SELECT count(*) FROM social_accounts WHERE status <> 'active'",
                "session healing exhausted its ladder — human re-login needed",
            )
        ],
    ),
    Worker(
        "tiktok-reconcile",
        "app.worker.tasks.tiktok_inbox_reconcile.reconcile_tiktok_inbox",
        "default",
        21600,
        goal="Reconcile TikTok inbox drafts with real publish state",
        guardrails=("never mark published without a live TikTok ID",),
        fallback="stale drafts surface as escalation",
        # reconcile marks stale inbox drafts on PUBLISHED targets — the
        # pending-status filter missed every one of them
        escalations=[
            (
                "stale tiktok inbox drafts",
                "SELECT count(*) FROM post_targets pt JOIN social_accounts sa "
                "ON sa.id = pt.social_account_id WHERE sa.platform='tiktok' "
                "AND pt.status='published' AND pt.error_message LIKE "
                "'Draft delivered to TikTok app inbox%'",
                "draft parked in the TikTok app inbox >24h — open the app and finish publishing, or discard",
            )
        ],
    ),
    Worker(
        "crm-engagement",
        "app.worker.tasks.crm_engagement.run_crm_engagement_sweep",
        "default",
        1800,
        goal="Sync social engagement signals into EspoCRM leads",
        guardrails=("leads without email are never queued for email",),
        fallback="per-lead error logged; sweep continues",
    ),
    Worker(
        "recurring-posts",
        "app.worker.tasks.recurring.process_recurring_posts",
        "default",
        300,
        goal="Materialize due recurring post templates into drafts",
        guardrails=("one instance per schedule slot — no duplicates",),
        fallback="slot missed → next sweep catches up",
        demand_driven=True,
        pending_sql="SELECT count(*) FROM posts WHERE is_recurring AND next_recurrence_at <= now()",
        evidence=[("last recurring materialized", "SELECT max(created_at) FROM posts WHERE recurrence_parent_id IS NOT NULL")],
    ),
    Worker(
        "dmr-keepwarm",
        "app.worker.tasks.dmr_health.keep_warm_dmr_models",
        "default",
        90,
        goal="Keep local DMR models warm so generation never cold-starts",
        guardrails=("never spend cloud tokens when a local model suffices",),
        fallback="generation falls back to Cloudflare Workers AI",
    ),
    Worker(
        "messenger-poll",
        "app.worker.tasks.personal_messenger.poll_personal_messenger",
        "messenger",
        120,
        goal="Reply to inbound Messenger chats within the cooldown policy",
        guardrails=(
            "bot disclosure on first contact",
            "5-minute per-thread cooldown",
            "deterministic handlers answer before the LLM",
        ),
        fallback="unanswerable → escalation to Themis",
        demand_driven=True,  # replies only arrive when inbound mail exists
    ),
    Worker(
        "email-drip",
        "app.worker.tasks.email_drip.run_email_drip_sweep",
        "default",
        DAY,
        goal="Advance email sequences on schedule",
        guardrails=(
            "unsubscribed/suppressed recipients are never mailed",
            "30-day dedup gate on campaign sends",
        ),
        fallback="send failure logged in email_logs; next sweep retries",
        # real drip volume = campaign recipients sent — email_logs is the
        # transactional stream (password resets would fake drip activity)
        evidence=[("last drip mail", "SELECT max(sent_at) FROM email_recipients WHERE status = 'sent'")],
    ),
    Worker(
        "lead-research",
        "app.worker.tasks.lead_research.run_lead_research_sweep",
        "default",
        DAY,
        goal="Enrich new CRM leads with company/role data",
        guardrails=("write only to the lead's own record — never merge without an idempotent key",),
        fallback="lead left un-enriched; sweep retries next day",
    ),
    Worker(
        "crm-lifecycle",
        "app.worker.tasks.crm_lifecycle.run_crm_lifecycle_sweep",
        "default",
        DAY,
        goal="Advance leads through the lifecycle stages",
        guardrails=("never regress a lead's stage",),
        fallback="stage transition failure logged; sweep continues",
    ),
    Worker(
        "crm-hygiene",
        "app.worker.tasks.crm_hygiene.run_crm_hygiene",
        "default",
        7 * DAY,
        goal="Dedupe and quarantine dirty CRM data weekly",
        guardrails=("quarantine beats delete — no bulk row removal",),
        fallback="conflicts parked for human review",
    ),
    Worker(
        "datalake-export",
        "app.worker.tasks.datalake_export.export_datalake",
        "default",
        21600,
        goal="Export analytics + CRM data to the R2 datalake",
        guardrails=("export-only: never mutate source tables",),
        fallback="export retried next sweep; partial files are not kept",
    ),
    Worker(
        "nextcloud-sync",
        "app.worker.tasks.nextcloud_sync.sync_nextcloud_surfaces",
        "default",
        10800,
        goal="Mirror operational state into Nextcloud Tables/Deck memory",
        guardrails=("never overwrite a human-edited Nextcloud row",),
        fallback="sync gap logged; next run retries",
    ),
    Worker(
        "strategy-brief",
        "app.worker.tasks.notebook_reports.run_notebook_report",
        "default",
        DAY,
        goal="Deliver the daily strategy brief grounded in real metrics",
        guardrails=("recommendations must cite real data — no fabricated windows or momentum",),
        fallback="brief skipped on data failure; report emails as failure",
        # notebook reports email out-of-band; content_briefs is a different
        # artifact — celery run-count is the evidence here
    ),
    Worker(
        "content-factory",
        "",
        "publishing",
        DAY,
        # no beat dispatches this — it's a composite label for generation
        # (n8n workflows + UI + recurring-posts). Unscheduled → quiet days
        # are not a missed beat.
        scheduled=False,
        goal="Produce trend-grounded, monetized drafts for every channel",
        guardrails=(
            "every draft carries correct media",
            "NLP score ≥80 before any draft can publish",
            "every CTA is UTM-tagged into the CRM funnel",
        ),
        autonomy="review",  # quality gate before publish
        fallback="generation failure → draft skipped, never posted empty",
        evidence=[("last post drafted", "SELECT max(created_at) FROM posts")],
    ),
]


async def audit() -> dict:
    from app.worker.celery_app import celery_app

    insp = celery_app.control.inspect(timeout=5)
    stats = insp.stats() or {}
    queues = insp.active_queues() or {}
    totals: dict[str, int] = {}
    for node, s in stats.items():
        for task, n in (s.get("total") or {}).items():
            totals[task] = totals.get(task, 0) + n
    live_nodes = list(stats)
    # queues with a responding consumer — a worker whose queue nobody
    # consumes is offline even if nothing else looks stale
    live_queues = {q.get("name") for node_qs in queues.values() for q in (node_qs or [])}
    # stats+queues both empty = inspect itself failed (broker down?) —
    # distinguishable from "one queue unstaffed"
    inspect_ok = bool(stats) or bool(live_queues)

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "celery_nodes": live_nodes,
        "celery_queues": sorted(live_queues),
        "inspect_ok": inspect_ok,
        "workers": [],
    }
    async with async_session_maker() as db:
        for w in WORKFORCE:
            entry = {
                "name": w.name,
                "task": w.task or "(composite)",
                "queue": w.queue,
                "cadence_s": w.cadence_s,
                "runs_seen": totals.get(w.task, 0) if w.task else None,
                "governance": {
                    "goal": w.goal,
                    "autonomy": w.autonomy,
                    "guardrails": list(w.guardrails),
                    "fallback": w.fallback,
                    "complete": bool(w.goal and w.guardrails and w.fallback),
                },
                "evidence": [],
                "escalations": [],
                "status": "ok",
                "scheduled": w.scheduled,
                "presence": w.queue in live_queues,
            }
            if not entry["governance"]["complete"]:
                entry["status"] = "ungoverned"
            pending = None
            if w.pending_sql:
                pending = (await db.execute(text(w.pending_sql))).scalar() or 0
                entry["pending"] = pending
            for label, sql in w.evidence:
                ts = (await db.execute(text(sql))).scalar()
                fresh = ts is not None and ts.replace(tzinfo=UTC) > (datetime.now(UTC) - timedelta(seconds=2 * w.cadence_s))
                # demand-driven: stale evidence only matters with work waiting
                if w.demand_driven and not pending:
                    fresh = True
                # unscheduled composites: staleness is context, never dark
                if not w.scheduled:
                    fresh = True
                entry["evidence"].append({"label": label, "at": str(ts), "fresh": fresh})
                if not fresh:
                    entry["status"] = "dark"
            for label, sql, why in w.escalations:
                n = (await db.execute(text(sql))).scalar() or 0
                entry["escalations"].append({"label": label, "count": n, "why": why})
                if n:
                    entry["status"] = "dark" if entry["status"] == "dark" else "escalation"
            # PRESENCE: nobody consuming the worker's queue = offline. When
            # inspect itself returned nothing, every worker is dark — a
            # clean report while all containers are down is a false pass.
            if not entry["presence"] or not inspect_ok:
                if entry["status"] in ("ok", "idle"):
                    entry["status"] = "dark"
            # runs_seen=0 means "no run since the worker's last restart" —
            # normal for daily/weekly beats, so it never flags on its own.
            if w.task and not totals.get(w.task) and entry["status"] == "ok":
                entry["status"] = "idle"
            # stats().total counts since worker START — a positive count
            # proves presence, not recency. With no evidence/escalation
            # artifact there is nothing to compare against cadence, so the
            # worker is present-but-unverified rather than silently 'ok'.
            if entry["status"] == "ok" and w.scheduled and not w.evidence and not w.escalations:
                entry["status"] = "unverified"
            if entry["status"] == "ok" and not w.scheduled:
                entry["status"] = "unverified"
            out["workers"].append(entry)
    out["ungoverned"] = [w["name"] for w in out["workers"] if w["status"] == "ungoverned"]
    return out


def render_governance(report: dict) -> str:
    lines = ["", "Global frame (binds every worker)", "-" * 78]
    for rule in GLOBAL_FRAME:
        lines.append(f"  ! {rule}")
    lines += ["", "Governance contract (goal · autonomy · fallback)", "-" * 78]
    for w in report["workers"]:
        g = w["governance"]
        mark = " " if g["complete"] else "!"
        lines.append(f"{mark} {w['name']:<17} [{g['autonomy']:>9}] {g['goal']}")
        for r in g["guardrails"]:
            lines.append(f"{'':>23}· {r}")
        if g["fallback"]:
            lines.append(f"{'':>23}→ on failure: {g['fallback']}")
    return "\n".join(lines)


def render(report: dict) -> str:
    lines = [
        f"Agent workforce audit — {report['generated_at'][:16]}Z",
        f"celery nodes: {len(report['celery_nodes'])}",
        "",
        f"{'worker':<18} {'runs':>5} {'status':<11} evidence / escalations",
        "-" * 78,
    ]
    if not report.get("inspect_ok", True):
        lines.append("⚠ celery inspect returned nothing — every worker treated as offline")
    for w in report["workers"]:
        runs = "-" if w["runs_seen"] is None else str(w["runs_seen"])
        bits = [f"{e['label']} @ {str(e['at'])[:16]}" + ("" if e["fresh"] else " (STALE)") for e in w["evidence"]]
        bits += [f"{e['label']}={e['count']}" for e in w["escalations"] if e["count"]]
        if not w.get("presence", True):
            bits.insert(0, f"no consumer on '{w['queue']}' queue")
        lines.append(f"{w['name']:<18} {runs:>5} {w['status']:<11} " + "; ".join(bits))
    bad = [w["name"] for w in report["workers"] if w["status"] in ("dark", "escalation")]
    unv = [w["name"] for w in report["workers"] if w["status"] == "unverified"]
    lines += ["", "attention: " + (", ".join(bad) if bad else "none — all workers clocked in")]
    if unv:
        lines.append("unverified (no output artifact to check): " + ", ".join(unv))
    return "\n".join(lines)


async def push_slack(report: dict) -> None:
    """Post the attention list — the paper's human-escalation lane."""
    from app.services.slack_notifications import post_alert_to_slack

    bad = [w for w in report["workers"] if w["status"] in ("dark", "escalation", "ungoverned")]
    if not bad:
        return
    lines = [
        "*Agent workforce needs a human*",
        *[
            f"• `{w['name']}` — {w['status']}"
            + ("".join(f" | {e['label']}={e['count']} — {e['why']}" for e in w["escalations"] if e["count"]) if w["status"] == "escalation" else "")
            for w in bad
        ],
    ]
    await post_alert_to_slack("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--governance", action="store_true", help="print the per-worker governance contract")
    ap.add_argument("--slack", action="store_true", help="post the attention list to Slack when non-empty")
    args = ap.parse_args()
    report = asyncio.run(audit())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
        if args.governance:
            print(render_governance(report))
    if args.slack:
        asyncio.run(push_slack(report))
    sys.exit(1 if any(w["status"] in ("dark", "escalation", "ungoverned") for w in report["workers"]) else 0)


if __name__ == "__main__":
    main()
