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
from typing import Any

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
            # liveness is the per-attempt account_sync event — fires for every
            # synced account every run. follower_snapshots is a change log with
            # a 7-day heartbeat: flat counts write nothing for days and would
            # read dark on a fully healthy worker.
            (
                "stalest synced account",
                "SELECT min(last) FROM ("
                "  SELECT social_account_id, max(occurred_at) AS last"
                "  FROM analytics_events WHERE event_type = 'account_sync'"
                "  GROUP BY social_account_id) s"
                "  JOIN social_accounts sa ON sa.id = s.social_account_id"
                "  WHERE sa.status = 'active'",
            ),
            (
                # post metrics only re-measure inside the publish window —
                # quiet accounts go stale by design, so per-account min()
                # reads dark forever. Output freshness is the real signal.
                "latest post-analytics write",
                "SELECT max(captured_at) FROM post_analytics_snapshots",
            ),
        ],
        escalations=[
            (
                "active accounts w/ stale analytics sync",
                "SELECT count(*) FROM ("
                "  SELECT sa.id FROM social_accounts sa"
                "  LEFT JOIN LATERAL ("
                "    SELECT max(occurred_at) AS last"
                "    FROM analytics_events ae"
                "    WHERE ae.social_account_id = sa.id"
                "    AND ae.event_type = 'account_sync') s ON true"
                "  WHERE sa.status = 'active'"
                "  AND sa.platform NOT IN ('whatsapp')"
                "  AND (s.last IS NULL"
                "       OR s.last < now() - interval '3 hours')) t",
                "per-account sync is failing — check the platform's analytics path (see follower-analytics-guard)",
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
                "SELECT count(*) FROM social_accounts WHERE status <> 'active' AND meta_data->>'deleted_at' IS NULL",
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
        # liveness = the per-sweep audit artifact; sent mail is demand-driven
        # output (zero qualifying recipients reads dark on a healthy worker)
        evidence=[
            (
                "last drip sweep",
                "SELECT max(created_at) FROM audit_logs WHERE action='email-drip' AND resource_type='sweep'",
            )
        ],
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
    # ── Autonomous agency pipeline (agent-led fixed-scope model) ──────
    Worker(
        "agency-front-desk",
        "app.worker.tasks.agency.run_agency_triage_sweep",
        "default",
        900,  # 15 min
        goal="Classify every new lead into the service catalog and queue a site audit",
        guardrails=(
            "explainable routing only — deterministic keyword model, matched signals recorded",
            "never email or contact a lead — triage writes notes and audit rows only",
        ),
        fallback="lead left untriaged; next sweep retries",
        demand_driven=True,
        evidence=[("last audit queued", "SELECT max(created_at) FROM agency_audits")],
        pending_sql="SELECT count(*) FROM leads WHERE NOT COALESCE(meta_data ? 'agency_triaged', false)",
    ),
    Worker(
        "agency-architect",
        "app.worker.tasks.agency.run_agency_audit_sweep",
        "default",
        600,  # 10 min
        goal="Deliver a deterministic site audit minutes after a lead arrives",
        guardrails=(
            "measured data only — every finding cites a real response/header",
            "SSRF-guarded fetches — public URLs only (url_safety)",
        ),
        fallback="audit row → failed with error; next sweep does not retry",
        demand_driven=True,
        evidence=[("last completed audit", "SELECT max(completed_at) FROM agency_audits")],
        pending_sql="SELECT count(*) FROM agency_audits WHERE status='pending'",
        escalations=[
            (
                "failed audits",
                "SELECT count(*) FROM agency_audits WHERE status='failed' AND created_at > now() - interval '7 days'",
                "site unreachable or rejected by SSRF guard — a human must confirm the lead's URL",
            ),
        ],
    ),
    Worker(
        "agency-proposals",
        "app.worker.tasks.agency.run_agency_proposal_sweep",
        "default",
        1800,  # 30 min
        goal="Turn every completed audit into a priced fixed-scope proposal draft",
        guardrails=(
            "never auto-send — a proposal leaves only via human approve",
            "prices come from the fixed catalog — never invent a number",
        ),
        autonomy="review",  # Slack approve/reject is the gate
        fallback="audit stays unproposed; next sweep retries",
        demand_driven=True,
        evidence=[("last proposal drafted", "SELECT max(created_at) FROM agency_proposals")],
        pending_sql="SELECT count(*) FROM agency_audits a WHERE a.status='completed' AND NOT EXISTS (SELECT 1 FROM agency_proposals p WHERE p.audit_id=a.id)",
        escalations=[
            (
                "stale drafts",
                "SELECT count(*) FROM agency_proposals WHERE status='draft' AND created_at < now() - interval '3 days'",
                "draft awaiting human decision — the front desk promised a fast answer",
            ),
        ],
    ),
    Worker(
        "agency-pm",
        "app.worker.tasks.agency.run_agency_pm_sweep",
        "default",
        DAY,
        goal="Drive every won engagement through the 14-day milestone plan",
        guardrails=(
            "client updates are drafts for a human — never mailed directly",
            "scope is fixed at kickoff — the agent never expands it",
        ),
        autonomy="review",
        fallback="milestone stays pending; next daily sweep retries",
        demand_driven=True,
        evidence=[("last client update", "SELECT max(last_client_update_at) FROM agency_projects")],
        pending_sql="SELECT count(*) FROM agency_projects WHERE status='active'",
        escalations=[
            (
                "overdue projects",
                "SELECT count(*) FROM agency_projects WHERE status='stalled'",
                "project passed its 14-day due date — a human must reset scope or close it",
            ),
        ],
    ),
    Worker(
        "agency-engineer",
        "app.worker.tasks.agency.run_agency_engineer_sweep",
        "default",
        DAY,
        goal="Turn every sent proposal into a reviewable PR manifest",
        guardrails=(
            "plans only — never write client code or open PRs autonomously",
            "every plan declares CI gate + rollback — no plan ships without both",
        ),
        autonomy="review",  # humans/Devin execute; codegen needs graduation
        fallback="proposal stays unplanned; next sweep retries",
        demand_driven=True,
        evidence=[
            (
                "last plan drafted",
                "SELECT max(updated_at) FROM agency_proposals WHERE meta_data ? 'engineering_plan'",
            )
        ],
        pending_sql="SELECT count(*) FROM agency_proposals WHERE status='sent' AND NOT COALESCE(meta_data ? 'engineering_plan', false)",
    ),
    Worker(
        "agency-followup",
        "app.worker.tasks.agency.run_agency_proposal_followup_sweep",
        "default",
        DAY,
        goal="Chase every sent proposal through the day-3/7/14 follow-up ladder",
        guardrails=(
            "fixed templates only — same plain voice as the proposal mail",
            "a reply or a closed-out stamp stops the ladder — never over-mail",
            "sends are real client mail — idempotent via meta_data steps",
        ),
        autonomy="auto",
        fallback="proposal stays at its step; next daily sweep retries",
        demand_driven=True,
        evidence=[
            (
                "last follow-up sent",
                "SELECT max(updated_at) FROM agency_proposals WHERE meta_data ? 'followup_history'",
            )
        ],
        pending_sql=(
            "SELECT count(*) FROM agency_proposals WHERE status='sent'"
            " AND NOT COALESCE(meta_data ? 'followed_out', false)"
            " AND NOT COALESCE(meta_data ? 'replied', false)"
        ),
        escalations=[
            (
                "unanswered proposals",
                "SELECT count(*) FROM agency_proposals WHERE status='sent'"
                " AND COALESCE(meta_data ? 'followed_out', false)"
                " AND NOT COALESCE(meta_data ? 'replied', false)",
                "full ladder ran with no reply — a human decides whether to call or close",
            ),
        ],
    ),
    Worker(
        "agency-closer",
        "app.worker.tasks.agency.run_agency_won_sweep",
        "default",
        3600,  # hourly
        goal="Turn every Closed-Won EspoCRM opportunity into a kicked-off 14-day project",
        guardrails=(
            "low-value Stripe checkouts never become projects (AGENCY_STRIPE_PROJECT_MIN_EUR)",
            "one project per opportunity — espo_opportunity_id + CRM note marker dedupe",
            "kickoff mail only — scope and milestones come from the fixed plan",
        ),
        autonomy="auto",
        fallback="opportunity stays un-projected; next hourly sweep retries",
        demand_driven=True,
        evidence=[("last project kickoff", "SELECT max(kickoff_at) FROM agency_projects")],
        escalations=[
            (
                "won deals without projects",
                "SELECT count(*) FROM agency_projects WHERE status='active' AND kickoff_at < now() - interval '1 day' AND last_client_update_at IS NULL",
                "project kicked off but no client update landed — check the PM sweep",
            ),
        ],
    ),
    Worker(
        "agency-post-consult",
        "app.worker.tasks.agency.run_agency_post_consult_sweep",
        "default",
        3 * 3600,  # every 3h
        goal="Send the thank-you/next-steps mail after every passed consultation",
        guardrails=(
            "one follow-up per opportunity — CRM note marker dedupe",
            "never mails when no contact email — notes the skip instead",
        ),
        autonomy="auto",
        fallback="opportunity stays unfollowed; next 3h sweep retries",
        demand_driven=True,
        evidence=[],
    ),
    Worker(
        "talk-meeting-notes",
        "app.worker.tasks.meeting_notes.run_meeting_notes_sweep",
        "default",
        1800,  # every 30 min
        goal="Every consultation gets a Talk room; every recorded call becomes CRM minutes",
        guardrails=(
            "rooms are public-type but token-gated links — no passwords stored",
            "minutes cite only what the transcript supports — never invented commitments",
            "recording consent is announced in the room welcome message",
            "marker notes dedupe — no duplicate rooms or repeated transcription",
        ),
        autonomy="auto",
        fallback="opportunity keeps Google Meet link only; next 30-min sweep retries",
        demand_driven=True,
        evidence=[],  # artifacts live in EspoCRM/Talk — outside Postgres
        pending_sql="SELECT 0",  # demand is EspoCRM-side (appointmentsscheduled opps)
    ),
    Worker(
        "analytics-digest",
        "app.worker.tasks.notebooks.run_weekly_analytics",
        "default",
        7 * DAY,
        goal="Weekly publish/agency/audit KPI digest → ops channel",
        guardrails=("read-only queries; single Slack post; no external sends",),
        autonomy="auto",
        fallback="skip on error; notebook twin in notebooks/weekly-analytics.ipynb",
        evidence=[("last proposal", "SELECT max(updated_at) FROM agency_proposals")],
        pending_sql="SELECT count(*) FROM agency_proposals WHERE status='draft'",
    ),
    Worker(
        "human-gate-watch",
        "app.worker.tasks.human_gate.run_human_gate_watch",
        "default",
        1800,
        goal="Surface blocked human gates (LinkedIn 2FA, Meta restriction) to ops",
        guardrails=(
            "read-only sidecar polls; Slack nudge ≤1/6h per gate (Redis dedup)",
            "never impersonate the owner — surface only",
        ),
        autonomy="auto",
        fallback="skip when sidecar asleep",
        evidence=[],
    ),
    Worker(
        "nurture-sweep",
        "app.worker.tasks.nurture.run_nurture_sweep",
        "default",
        DAY,
        goal="Advance public leads through the 3-touch nurture sequence",
        guardrails=(
            "only public-capture leads (form newsletter/free-audit); unsubscribe + opt-out respected",
            "sequential steps stamped in meta_data — no duplicates",
        ),
        autonomy="auto",
        fallback="lead skipped; next daily sweep retries",
        evidence=[("last step sent", "SELECT max((meta_data->>'nurture_step_at')::timestamptz) FROM leads WHERE meta_data ? 'nurture_step_at'")],
        pending_sql=(
            "SELECT count(*) FROM leads WHERE meta_data->>'form' IN ('newsletter','free-audit') "
            "AND NOT COALESCE(meta_data ? 'nurture_step', false) OR (meta_data->>'nurture_step')::int < 3"
        ),
    ),
    Worker(
        "newsletter-subs-sync",
        "app.worker.tasks.newsletter_subs.run_newsletter_subscriber_sync",
        "default",
        7 * DAY,
        goal="Pull LinkedIn newsletter subscribers into EspoCRM",
        guardrails=(
            "read-only voyager call via sidecar cookies; LI-URN dedupe — never double-create",
            "no email means no outreach — these are awareness-stage contacts",
        ),
        autonomy="auto",
        fallback="skip when sidecar session dead (2FA gate)",
        evidence=[("last sync", "SELECT max(created_at) FROM leads WHERE meta_data->>'form'='linkedin-newsletter'")],
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
        "nextcloud-docs-sync",
        "app.worker.tasks.nextcloud_docs.sync_nextcloud_docs_task",
        "default",
        21600,
        goal="Keep Nextcloud Files stocked with the latest repo documentation/architecture",
        guardrails=(
            "read-only on GitHub master — never pushes or mutates the repo",
            "uploads only *.md under root/docs/deploy/.devin, capped per file",
            "never writes outside SocialAuto/docs/repo/ in the workspace",
        ),
        fallback="sync gap logged; next run retries — docs are never lost, just stale",
    ),
    Worker(
        "central-brain",
        "app.worker.tasks.central_brain.run_central_brain",
        "default",
        DAY,
        goal=(
            "Decide each day's prioritized per-channel actions from live signals "
            "(devapp health, engagement, followers, market trends) and dispatch "
            "them through the report executor"
        ),
        guardrails=(
            "unhealthy channels never generate posts — health decisions only",
            "trend 'noise' classifications are ignored; only opportunity/early_window move the plan",
            "dispatch dedups on meta_data.central_brain_action — reruns never double-post",
            "max 5 post actions per team per day",
        ),
        autonomy="auto",
        fallback="sweep failure logged per team; next daily run retries — plan is journaled to company_brain either way",
        escalations=[
            (
                "devapp snapshots reporting ok=false",
                "SELECT count(*) FROM analytics_events WHERE event_type='devapp_snapshot' "
                "AND meta_data->>'ok' = 'false' AND occurred_at > now() - interval '2 days'",
                "a connected developer app is failing its health pull — needs token re-auth or scope repair before the channel can publish again",
            )
        ],
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
        "failure-learner",
        "app.worker.tasks.failure_learning.run_failure_learning_sweep",
        "default",
        DAY,
        goal="Convert recurring publish-failure signatures into remediation proposals",
        guardrails=(
            "proposals only — never auto-apply a fix or edit production code",
            "read-only on post_targets; writes are audit_log artifacts only",
            "dedupe proposals inside the cooldown — never spam the same signature",
        ),
        autonomy="review",  # proposals land for a human; nothing self-executes
        fallback="sweep failure logged; next daily run retries",
        evidence=[
            (
                "last learning sweep",
                "SELECT max(created_at) FROM audit_logs WHERE action='failure-learning' AND resource_type='sweep'",
            )
        ],
    ),
    Worker(
        "voice-learner",
        "app.worker.tasks.edit_diff.run_edit_diff_sweep",
        "default",
        7 * DAY,
        goal="Distill recurring human edits into voice_signature suggestions",
        guardrails=(
            "suggestions only — never edit voice_signature or brand_voice directly",
            "read-only on posts; writes are audit_log artifacts only",
            "single edits are taste, not signal — patterns need >=2 posts at >=40% share",
        ),
        autonomy="review",
        fallback="sweep failure logged; next weekly run retries",
        evidence=[
            (
                "last edit-diff sweep",
                "SELECT max(created_at) FROM audit_logs WHERE action='edit-diff' AND resource_type='sweep'",
            )
        ],
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
    Worker(
        "kakkava-sync",
        "app.worker.tasks.kakkava_sync.sync_kakkava_corpus",
        "default",
        DAY,
        goal="Keep team_<id>_doctrine Chroma collections in sync with the Kakkava corpus folder",
        guardrails=(
            "read-only on the corpus mount — never writes to KAKKAVA_DIR",
            "fail-silent: missing folder or Chroma outage yields empty stats, never crashes the sweep",
            "idempotent — content-hash skips mean re-runs are safe",
        ),
        autonomy="auto",
        fallback="per-team sync failure logged in worker output + audit_logs row records errors count",
        evidence=[
            (
                "last corpus sync",
                "SELECT max(created_at) FROM audit_logs WHERE action='kakkava-sync' AND resource_type='sweep'",
            )
        ],
    ),
    Worker(
        "funnel-audit",
        "app.worker.tasks.funnel_audit.run_funnel_audit",
        "default",
        7 * DAY,
        goal="Report funnel stage counts + weakest transition weekly so the loop is observable",
        guardrails=(
            "read-only — runs the audit script as a subprocess, writes nothing to CRM",
            "exit-1 (broken stage) escalates to the alert lane, success goes to the digest lane",
            "compact report — last 40 lines only, never floods the channel",
        ),
        autonomy="auto",
        fallback="subprocess failure or Slack post failure logs a warning; next weekly run retries",
        evidence=[
            (
                "last funnel audit report",
                "SELECT max(created_at) FROM audit_logs WHERE action LIKE 'funnel-audit%'",
            )
        ],
    ),
    Worker(
        "linkedin-adlib-watch",
        "app.worker.tasks.linkedin_intel.run_linkedin_ad_library_watch",
        "default",
        7 * DAY,
        goal="Weekly Ad Library sweep → linkedin_ad_intel rows + #intel digest",
        guardrails=(
            "read-only on LinkedIn — never writes to the platform",
            "token decrypted in-process only; never logged or emitted",
            "5s page pacing, ≤10 pages per watch — stays under API fair-use",
        ),
        autonomy="auto",
        fallback="per-watch errors collected into the digest; task failure → celery retry/alert",
        evidence=[
            ("latest ad observation", "SELECT max(last_seen_at) FROM linkedin_ad_intel"),
        ],
        escalations=[
            (
                "enabled watches not running",
                "SELECT count(*) FROM linkedin_intel_watch WHERE enabled AND (last_run_at IS NULL OR last_run_at < now() - interval '14 days')",
                "watch list exists but the sweep isn't reaching it — check worker logs / token",
            )
        ],
    ),
    Worker(
        "nextcloud-docs",
        "app.worker.tasks.nextcloud_docs.sync_nextcloud_docs_task",
        "default",
        6 * 3600,
        goal="Keep Nextcloud Files carrying the latest merged documentation/architecture",
        guardrails=(
            "reads the manifest from GitHub master only — never the dirty checkout",
            "per-file blob SHA dedupe — only changed docs re-upload",
            "512KB/file cap and 500-file ceiling — fail loud, never partial-forever",
        ),
        fallback="per-file upload errors logged; _manifest.json still written; next run retries diffs",
    ),
    Worker(
        "slack-ops-journal",
        "app.worker.tasks.slack_ops_journal.slack_ops_journal_task",
        "default",
        DAY,
        goal="Journal the last 24h of Slack alert/ops channels into Nextcloud Notes + company brain",
        guardrails=(
            "read-only on Slack — conversations.history only, never posts",
            "one dead channel degrades to an empty section, never sinks the journal",
            "note write is create-or-update by title — idempotent per day",
        ),
        fallback="note/memory failures logged per-surface; task failure → celery retry",
    ),
    # ── Auto-engage pipeline (PR #1578) ─────────────────────────────────
    # Ingest feeds candidates → dispatch scores/queues/acts. AUTONOMY_LEVEL
    # env is the human-gate dial (0=manual, 3=full auto, live at 3).
    Worker(
        "engagement-dispatch",
        "app.worker.tasks.engagement.dispatch_engagements",
        "default",
        600,
        goal="Execute queued engagement actions (like/repost/comment) through platform actors",
        guardrails=(
            "sentiment kill-switch — never engage sensitive posts",
            "per-platform daily caps + FB 5 comments/hour ceiling",
            "target posts need SEO>=ENGAGE_MIN_SCORE; drafts need SEO+NLP>=DRAFT_MIN_SCORE",
            "comments/reposts honor the AUTONOMY_LEVEL human gate",
        ),
        autonomy="auto",
        fallback="actor failure → status=failed/pending_actor; gated items expire after 7d",
        demand_driven=True,
        evidence=[
            ("latest queued action", "SELECT max(created_at) FROM engagement_actions"),
            ("latest acted action", "SELECT max(acted_at) FROM engagement_actions"),
        ],
        pending_sql="SELECT count(*) FROM engagement_actions WHERE status IN ('pending','pending_actor')",
        escalations=[
            (
                "gated actions stuck >2d",
                "SELECT count(*) FROM engagement_actions WHERE status='pending_human_gate' AND created_at < now() - interval '2 days'",
                "autoapprove should clear these every 10m — stuck means the dial is low or dispatch is failing",
            ),
            (
                "failed actions 24h",
                "SELECT count(*) FROM engagement_actions WHERE status='failed' AND created_at > now() - interval '24 hours'",
                "actor errors need inspection — check platform sessions and the browser bridge",
            ),
            (
                "pending_actor stuck >24h",
                "SELECT count(*) FROM engagement_actions WHERE status='pending_actor' AND created_at < now() - interval '24 hours'",
                "no actor consumed it — platform actor missing or session asleep",
            ),
        ],
    ),
    Worker(
        "engagement-autoapprove",
        "app.worker.tasks.auto_gates.engagement_autoapprove",
        "default",
        600,
        goal="Auto-approve pending_human_gate engagement actions per AUTONOMY_LEVEL",
        guardrails=(
            "level 2 approves only comment/duet_flag with a draft and score>=60",
            "level 3 approves everything — the human gate becomes an audit log",
            "level <2 is a no-op — manual approval stays the path",
        ),
        autonomy="auto",
        fallback="off at level<2 — gated actions simply wait for a human",
        escalations=[
            (
                "gated actions stuck >2d",
                "SELECT count(*) FROM engagement_actions WHERE status='pending_human_gate' AND created_at < now() - interval '2 days'",
                "the 10m sweep should have approved these — check AUTONOMY_LEVEL and worker logs",
            )
        ],
    ),
    Worker(
        "engagement-ingest-keywords",
        "app.worker.tasks.engagement.engagement_ingest",
        "default",
        3600,
        goal="Feed the funnel — X keyword search → engagement_candidates",
        guardrails=("read-only on X — search only, never posts", "dedupes on post_id"),
        fallback="no bearer token → returns skipped; next hour retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-ingest-x-feed",
        "app.worker.tasks.engagement_ingest.ingest_x_feed",
        "default",
        3600,
        goal="Ingest fresh posts from the X home timeline → engagement_candidates",
        guardrails=("read-only timeline pull via REST v2", "dedupes on post_id"),
        fallback="API error → task retry once; next hour retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-ingest-li-feed",
        "app.worker.tasks.engagement_ingest_li.ingest_linkedin_feed",
        "default",
        3600,
        goal="Scrape the LinkedIn feed via the browser sidecar → engagement_candidates",
        guardrails=(
            "sidecar rate-limit cooldown honored — skips cleanly when cooling",
            "personal-feed read only — never writes to LinkedIn",
        ),
        fallback="sidecar down/busy → skipped result; next hour retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-ingest-ig-feed",
        "app.worker.tasks.engagement_ingest_ig.ingest_ig_feed",
        "default",
        3600,
        goal="Scrape Instagram feed articles via the shared browser bridge → engagement_candidates",
        guardrails=(
            "skips cleanly when the bridge is busy or the session is logged out",
            "read-only — never writes to Instagram",
        ),
        fallback="bridge busy/login-wall → skipped; next hour retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-ingest-tt-tags",
        "app.worker.tasks.engagement_ingest_tt.ingest_tiktok_tags",
        "default",
        1800,
        goal="Scrape TikTok hashtag feeds via the browser bridge → candidates, viral flagged for duet",
        guardrails=(
            "viral-breakout candidates (>5k views) surface as gated duet_flag, never auto-duet",
            "read-only scrape — never writes to TikTok",
        ),
        fallback="bridge busy → skipped; next sweep retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-ingest-fb-groups",
        "app.worker.tasks.engagement_ingest_fb.ingest_fb_groups",
        "default",
        1800,
        goal="Scrape whitelisted Facebook group feeds → engagement_candidates",
        guardrails=(
            "only FACEBOOK_GROUP_URLS groups are touched",
            "Meta 5-comments/hour cap enforced at queue time",
            "read-only scrape — never writes to Facebook",
        ),
        fallback="no groups configured → skipped; next sweep retries",
        evidence=[("latest candidate ingested", "SELECT max(ingested_at) FROM engagement_candidates")],
    ),
    Worker(
        "engagement-tiers",
        "app.worker.tasks.engagement_tiers.update_connection_tiers",
        "default",
        DAY,
        goal="Recompute RYR reciprocity tiers from real engagement outcomes + CRM",
        guardrails=("read-only on EspoCRM — tiers derive from stored actions",),
        fallback="CRM unreachable → tiers keep last values; next day retries",
    ),
    Worker(
        "engagement-archive",
        "app.worker.tasks.engagement_archive.compact_ledger",
        "default",
        DAY,
        goal="Compact engagement ledger rows >30d — keep outcome signal, drop bulky text",
        guardrails=("quarantine beats delete — nulls text fields, never deletes rows",),
        fallback="single UPDATE per table; failure → next day retries",
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
            governed = bool(w.goal and w.guardrails and w.fallback)
            entry: dict[str, Any] = {
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
                    "complete": governed,
                },
                "evidence": [],
                "escalations": [],
                "status": "ok",
                "scheduled": w.scheduled,
                "presence": w.queue in live_queues,
            }
            if not governed:
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
        await _persist_verdicts(db, out)
        out["autonomy_proposals"] = await _autonomy_proposals(db)
    out["ungoverned"] = [w["name"] for w in out["workers"] if w["status"] == "ungoverned"]
    return out


# ── Earned autonomy ladder (OpenMAO pattern: autonomy is earned, not assumed) ──
# Every audit run persists one verdict row per worker into audit_logs — that
# history IS the worker's track record. Ladder tiers: supervised → review →
# auto. A streak of clean audits proposes graduation one tier up; a recent
# dark/escalation/ungoverned verdict on an "auto" worker proposes narrowing.
# Proposals are advisory — a human edits the Worker contract to apply them.

AUTONOMY_LADDER = ("supervised", "review", "auto")
GRADUATE_AFTER = 10  # consecutive clean audits to propose promotion
DEMOTE_WITHIN = 7  # recent runs scanned for failure verdicts
VERDICT_LOOKBACK_DAYS = 90
_CLEAN = {"ok", "idle"}
_BAD = {"dark", "escalation", "ungoverned"}


async def _persist_verdicts(db, report: dict) -> None:
    """One audit-log verdict row per worker per run — the track record."""
    from app.models.user import AuditLog

    team_id = (await db.execute(text("SELECT id FROM teams ORDER BY created_at LIMIT 1"))).scalar()
    if not team_id:
        return
    for w in report["workers"]:
        db.add(
            AuditLog(
                team_id=team_id,
                action="workforce-verdict",
                resource_type="worker",
                resource_id=w["name"],
                detail=w["status"],
                meta={"status": w["status"], "autonomy": w["governance"]["autonomy"]},
            )
        )
    await db.commit()


async def _autonomy_proposals(db) -> list[dict]:
    """Read the verdict history and emit graduation/demotion proposals."""
    from sqlalchemy import select

    from app.models.user import AuditLog

    since = datetime.now(UTC) - timedelta(days=VERDICT_LOOKBACK_DAYS)
    rows = (
        await db.execute(
            select(AuditLog.resource_id, AuditLog.meta)
            .where(AuditLog.action == "workforce-verdict", AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
        )
    ).all()
    history: dict[str, list[str]] = {}
    for rid, meta in rows:
        history.setdefault(rid, []).append((meta or {}).get("status", "ok"))

    proposals: list[dict] = []
    by_name = {w.name: w for w in WORKFORCE}
    for name, verdicts in history.items():
        w = by_name.get(name)
        if not w:
            continue
        tier = AUTONOMY_LADDER.index(w.autonomy) if w.autonomy in AUTONOMY_LADDER else -1
        streak = 0
        for v in verdicts:
            if v in _CLEAN:
                streak += 1
            else:
                break
        recent_bad = [v for v in verdicts[:DEMOTE_WITHIN] if v in _BAD]
        if recent_bad and w.autonomy == "auto":
            proposals.append(
                {
                    "worker": name,
                    "direction": "demote",
                    "from": w.autonomy,
                    "to": "review",
                    "why": f"{len(recent_bad)} failure verdict(s) in last {DEMOTE_WITHIN} audits — narrow scope until clean",
                }
            )
        elif streak >= GRADUATE_AFTER and 0 <= tier < len(AUTONOMY_LADDER) - 1:
            proposals.append(
                {
                    "worker": name,
                    "direction": "graduate",
                    "from": w.autonomy,
                    "to": AUTONOMY_LADDER[tier + 1],
                    "why": f"{streak} consecutive clean audits — track record supports widening",
                }
            )
    return proposals


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
    props = report.get("autonomy_proposals") or []
    if props:
        lines.append("")
        lines.append("autonomy proposals (advisory — edit the Worker contract to apply):")
        for p in props:
            lines.append(f"  {'▲' if p['direction'] == 'graduate' else '▼'} {p['worker']}: {p['from']} → {p['to']} — {p['why']}")
    return "\n".join(lines)


async def push_slack(report: dict) -> None:
    """Post the attention list — the paper's human-escalation lane."""
    from app.services.slack_notifications import post_alert_to_slack

    bad = [w for w in report["workers"] if w["status"] in ("dark", "escalation", "ungoverned")]
    props = report.get("autonomy_proposals") or []
    if not bad and not props:
        return
    lines = []
    if bad:
        lines += [
            "*Agent workforce needs a human*",
            *[
                f"• `{w['name']}` — {w['status']}"
                + ("".join(f" | {e['label']}={e['count']} — {e['why']}" for e in w["escalations"] if e["count"]) if w["status"] == "escalation" else "")
                for w in bad
            ],
        ]
    if props:
        if lines:
            lines.append("")
        lines.append("*Autonomy ladder — proposals (advisory)*")
        lines += [f"• `{p['worker']}` {p['direction']}: {p['from']} → {p['to']} — {p['why']}" for p in props]
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
