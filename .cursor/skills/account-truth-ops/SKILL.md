---
name: account-truth-ops
description: >
  Per-account DB-status vs live-platform probe — reports FALSE-ACTIVE drift
  (heal lanes writing status='active' without token validation) and
  UNDERSTATED rows before publishes fail. Runs the same _probe as
  account_status_reconcile but as a report, not a writer. Use after Meta
  password changes, healer storms, or whenever social_accounts.status looks
  healthier than reality. Tool: backend/scripts/account_truth.py.
---

# Account truth ops — DB says vs platform says

`social_accounts.status` is write-multi-path: connect flows, heal lanes,
session checkers and `account_status_reconcile` (6h beat) all touch it.
Observed failure (2026-11-15): Meta password change killed every token,
yet heal lanes flipped IG/Threads/WhatsApp rows back to `active` —
`debug_token` said 190 Bad signature while the DB claimed healthy.

`account_status_reconcile` probes live but only writes the DB and
**deliberately never promotes bad→active** ("session health owns" that
transition). Nothing reported the *diff*. This script is the diff.

```bash
# inside the repo root (WSL)
docker compose exec -T social-api python scripts/account_truth.py
docker compose exec -T social-api python scripts/account_truth.py --json
docker compose exec -T social-api python scripts/account_truth.py --platform meta
```

Output: one row per account — `db_status`, live `probe` result
(`active|revoked|inconclusive|expired-by-clock|no-token`), verdict:

- `MATCH` — DB and platform agree
- `FALSE-ACTIVE` — db=active/connected but live probe says revoked →
  **exit 1**, publishes on this account will fail; find which heal lane
  wrote the status (check `meta_data.last_verified_at` vs
  `token_probe_status`)
- `UNDERSTATED` — db=expired/revoked but token actually works → drift the
  other way; reconcile won't promote it by design
- `UNKNOWN` — inconclusive probe / no token / unmapped platform; never
  fails the run

## Rules

- Read-only: it never writes `status`. Fix drift by healing the token
  (meta-oauth-ops, linkedin-reauth-ops, x-oauth-wire, …) or letting
  `reconcile_account_status` flip provably-dead rows.
- Probes are the same cheapest-authoritative reads reconcile uses —
  one GET per account, ~15s timeout each, run concurrently.
- `expired-by-clock` mirrors reconcile: `token_expires_at` in the past
  means expired without a network call.
- Pair with `dev-app-audit` (tool-level health) — truth says *which
  account rows lie*; app_audit says *which developer apps/credentials*
  are broken.
