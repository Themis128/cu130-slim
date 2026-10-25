# BACKLOG (living)

## Done 2026-10-25
- Platform-aware nurture (PLATFORM_SUBJECTS/OPENERS, meta_data.platform) + survey platform field fix (#909)
- Video ops: trim/convert/attach-audio/mute/extract-audio + mkv/avi upload (#908) + video-ops.py CLI
- Survey funnel live: /survey page + /api/survey → leads/public/survey (cloudless.gr #2289)
- Chatbot bookings → funnel leads (cloudless.gr #2293)
- kubectl SSH shim on 25+ workflows → k3s reachable (#2286/#2287/#2291); cluster-doctor real output: NO analytics namespace on k3s — Metabase/duckdb aren't there (likely plain docker on omv or removed)
- Tailscale ping param removed from 44 workflows (#2292) — upstream flake #263
- nginx metrics resolver + stack-ops /repo env paths (#901); x→twitter alias (#903); mypy 1.15 (#905); mail signature (send-mail.py + Nextcloud sync script); AGENTS.md media-sound rule (#907)
- Metabase stale queries diagnosed: omv Metabase queries n8n DB with 1.x columns (shared/nodesAccess/role/is_superuser) removed in 2.43 — fix SQL in stack-doctor/metabase-stale-queries.md

## Leftovers (human gates)
- X re-auth: click the /connect/twitter authorize URL (works via #903)
- Rotate LinkedIn password + li_at (exposed in session logs)
- LinkedIn Community Management API: dedicated 2nd app → verify page → Products request (human-gate-ops)
- Mailbox password MAIL_TBALTZAKIS_PASSWORD for read-inbox.py triage
- Apply metabase-stale-queries.md SQL on omv Metabase (needs omv SSH/CI path)
- IntraVox: no app folder yet — Funnels/ docs ready to pull into pages when intranet is created
- Stacked-branch PR conflicts recur — prefer fresh-off-master topic branches
