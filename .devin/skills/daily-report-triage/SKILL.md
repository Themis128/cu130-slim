---
name: daily-report-triage
description: >-
  Triage the daily operational reports landing in tbaltzakis@cloudless.gr —
  SocialAuto ops health / strategy brief / monetization / daily report,
  LinkedIn ad report, shop-online rolling credit digest, OMV maintenance +
  pending-config notices. Classify each alert as real failure vs stale /
  historical / platform-limit / dedup, verify against live services, then
  dispatch the fix to the right skill. Use when the user asks "check today's
  reports", "what needs addressing", or after the morning report batch.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - write
  - edit
triggers:
  - user
  - model
---

# Daily Report Triage

The mailbox `tbaltzakis@cloudless.gr` receives ~15-20 report mails/day.
The job: read them, decide what's real, verify live, fix or escalate.
Never trust a report's numbers at face value — verify against the live
service/database before acting.

## Step 1 — Read the mailbox

Use `omv-ha-mail` skill's `read-inbox.py` (IMAP 192.168.1.130:993).
Auth: `SMTP_PASSWORD` env var doubles as the dovecot SASL password —
there is no `MAILBOX_PASSWORD` var. Filter on today's date; expect:

| Sender/subject | What it is |
|---|---|
| `[SocialAuto] Ops health` | publish failures, stale syncs, anomalies |
| `[SocialAuto] Strategy brief` | playbook actions + data-gap warnings |
| `SocialAuto monetization report` | follower counts per account |
| `LinkedIn ad report` | campaign spend vs promo credit |
| `shop-online · rolling 60 min` | hourly credit-window digest |
| `[SocialAuto] Daily report` | overall OK/issues rollup |
| OMV maintenance/heartbeat/pending | host ops state |

## Step 2 — Classification signatures

Things that look alarming but are NOT failures:

- **duplicate-content skips** — the 30-day dedup guard working as
  designed (`publish-ops` skill)
- **strategy brief `twitter stats HTTP 401`** — the brief generates at
  06:00Z; token refresh landed later. Verify token live before acting
- **X followers "stale"** — @TBaltzakis genuinely has 0 followers; the
  series legitimately records real zeros now (#684). A 0-count snapshot
  is data, not an error
- **linkedin member sync stale** — personal-profile analytics need the
  `member_postAnalytics` scope we don't have; follower-only cadence is
  expected. Browser-scrape path fills the gap
- **quota_exhausted notes** — correct free-tier data-gap markers
- **blank-subject OMV mails** — mail-alias workflow test sends

Things that ARE failures needing action:

| Signature | Root cause found | Skill / fix |
|---|---|---|
| `publish_cancelled` / `spam_risk_too_many_pending_share` (TikTok) | MEDIA_UPLOAD inbox drafts unconfirmed, clogging the 5-pending window | `check-pending.py --cancel` (tiktok-console-ops); finish DIRECT_POST audit to kill the flow |
| `url_ownership_unverified` (TikTok) | predates cloudless.gr verification | verify domain state in console — usually stale |
| `linkedin member scrape: <empty>` | sidecar wedged/OOMKilled | restart sidecar, check `/health` (`browser_connected`, `page_ok`) — browser-ops |
| LinkedIn credit exhausted | shared credit drains across ALL campaigns | Marketing API spend is authoritative; digest uses `creditSpendEur` (all-time) vs windowed pace |
| OMV pending configuration | changes made, not applied | `omv-ops` skill → `omv-apply-pending.yml` workflow |
| Slack webhook `404 no_service` | app uninstalled | regenerate at api.slack.com — manual only |

## Step 3 — Verify live before acting

- Publish failures: query `post_targets` joined to `social_accounts`
  in Postgres (metabase MCP or `docker compose exec social-api`).
  Check `created_at` — anything older than ~48h is historical.
- Token health: `x-api-ops`/`tiktok-api-ops`/`meta-graph-ops` scripts
  (`*_tool.py`) — a live 200 + quota remaining = healthy.
- Ads: Marketing API `collect_metrics()` gives account-wide spend;
  CM billing page via sidecar 9225 `/debug/navigate` is the fallback
  for the credit ledger (see `linkedin-ads-ops` — CM may SPA-error
  under automation; session re-auth is manual).
- Sidecars: `/health` now reports `browser_connected` + `page_ok`;
  a wedged-but-running container fails these despite `status:ok`.

## Step 4 — Report format

Rank by severity: money > publish > analytics > informational. State
what was verified live vs assumed. One line per report, verdict
table, then the action list.

## Signatures added 2026-10-19 (ops-health report triage)

- **`failed` count ≠ real failures** — a `post_targets.status='failed'` row
  alongside a `published` row for the same platform is historical/duplicate
  noise. Truth source: `posts.metadata->'publish_summary'`
  (`total/published/failed`) + the completed target's `platform_post_id`.
- **Quality-gate skips are content fixes, not platform outages** — target
  error strings like `Quality gate: seo_score 75/100 below minimum 80` or
  `Facebook post has no media` mean rewrite the copy (Sofia NLP ≥80) and/or
  attach media, verify the asset wasn't already used on that account
  (media dedup), then requeue that ONE target — `skipped` target rows are
  overwritten on retry, safe to requeue.
- **Stale `processing` lock after Postgres restart** — queue shows
  `processing=1` with no running task → orphaned lock; verify no
  `platform_post_id` first, then reset to `pending` (procedure in
  `publish-ops` → "Stale publish-queue locks").
- **Advisory-check failures don't block** — LanguageTool 503
  (`stack-ops:8010`), DMR cold-start, Slack notify `channel_not_found` are
  noise for publish success but belong on `.devin/BACKLOG.md` as ops items.
