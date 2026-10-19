---
name: agent-workforce
description: >-
  The autonomous-company org chart — every scheduled Celery beat worker as a
  bounded "digital worker" with domain, cadence, observable output, and a
  human-escalation lane. Covers scripts/workforce_audit.py (live presence +
  output + escalation check), the worker roster in app/worker/celery_app.py,
  and how to onboard a new worker with an evidence contract. Use when asking
  "is the automation actually running", "which worker owns X", adding a beat
  task, or triaging silent-worker failures.
---

# Agent Workforce

Adopted framework (Dalton, *The Autonomous Enterprise*): the company runs on
**digital workers**, not scripts — each owns a bounded domain, runs on a
cadence, leaves observable work product, and parks exceptions for a human.
This skill is the org chart + the audit tool that verifies they clocked in.

## The tool — `scripts/workforce_audit.py`

```bash
docker exec -w /app social-api python scripts/workforce_audit.py          # report
docker exec -w /app social-api python scripts/workforce_audit.py --json   # machine
```

Per worker it verifies three live signals:

1. **Presence** — the Celery node exists and the task has run since worker
   start (`inspect().stats().total`).
2. **Output** — latest domain-table evidence within 2× cadence
   (e.g. `follower_snapshots.captured_at` for analytics-sync).
3. **Escalation** — parked items the worker can't self-resolve (stale
   processing locks, expired tokens, non-active accounts).

Statuses: `ok` · `idle` (no runs since restart — normal for daily/weekly
beats) · `dark` (periodic worker produced nothing in 2× cadence, or a
demand-driven worker has pending work but no recent output) · `escalation` ·
`ungoverned` (missing goal/guardrails/fallback in its `Worker` contract — see
`agentic-bpm` skill).
Exit code 1 when any worker is dark/escalation/ungoverned — safe for cron +
digests. `--governance` prints each worker's declared contract, `--slack`
pushes the attention list to Slack.

**Demand-driven rule:** publisher/scheduler/messenger workers only look
"dark" when `pending_sql` shows waiting work AND no recent output — an idle
queue is healthy, not absent. Don't flag a worker for having no work to do.

## Roster (live in `celery_app.py` beat_schedule)

| Worker | Task | Cadence | Evidence table |
|---|---|---|---|
| publisher | process_publish_queue | 30s | publish_queue.locked_at |
| scheduler | check_scheduled_posts | 60s | queue claims |
| analytics-sync | sync_all_analytics | 30m | follower_snapshots / post_analytics_snapshots |
| token-refresh | refresh_expiring_tokens | :15/:45 | social_accounts.token_expires_at |
| session-healer | heal_sessions | hourly :20 | accounts ≠ active = escalation |
| messenger-poll | poll_personal_messenger | 2m | (demand-driven) |
| crm-engagement | run_crm_engagement_sweep | 30m | (reply notes in EspoCRM) |
| email-drip | run_email_drip_sweep | daily | email_logs |
| lead-research / crm-lifecycle | sweeps | daily | EspoCRM notes/convert |
| crm-hygiene | run_crm_hygiene | Mon 07:30 | lead dedupe/quarantine |
| datalake-export | export_datalake | 6h | R2 lake/ |
| nextcloud-sync | sync_nextcloud_surfaces | 3h | Tables/Deck rows |
| strategy/monetization/ops-health | run_notebook_report | daily | emailed notebook |
| dmr-keepwarm | keep_warm_dmr_models | 90s | (no artifact — stats only) |

## Onboarding a new digital worker (the contract)

A beat task earns "worker" status only when it has all four:

1. **Bounded domain** — one clear job (book's rule: specialized agents beat
   generalists).
2. **Cadence** — declared in beat_schedule; the audit flags drift >2×.
3. **Observable output** — a DB artifact, external write, or Slack/email.
   If it leaves none, add one (a row, a timestamp, an audit_log entry) —
   silent workers are invisible failures.
4. **Escalation lane** — the failure mode that parks to a human
   (`post_alert_to_slack`, `error_message`, or an audit escalation query).

5. **Governance contract** (Agentic BPM, `agentic-bpm` skill) — `goal`,
   `guardrails`, `autonomy` tier, `fallback` in the `Worker(...)` entry;
   missing fields → `ungoverned`.

Then add a `Worker(...)` entry to `WORKFORCE` in the script — evidence +
escalation SQL, `demand_driven=True` if it only works when items wait, plus
the governance fields above.

## First-run findings (2026-10-19)

- `analytics-sync` dark for followers — FB last snap 10-13, IG 10-14, LI
  10-15, X 10-17; only telegram/threads/tiktok fresh. Post analytics are
  current → the follower path specifically is failing (see
  `follower-analytics-guard` for endpoint truth-table + repair).
- `session-healer` escalation — 1 non-active social account parked.
