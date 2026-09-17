---
name: d1-sync-ops
description: Operate and repair the Cloudflare D1 ↔ PostgreSQL dual-write sync. Covers the D1 free-tier daily row-write limit and write budget, the automatic PRIMARY KEY repair in db_sync.py, checking per-table PK presence, replaying queued writes after failover, and the /api/v1/cf-db/* endpoints. Use when D1 sync fails, writes are refused with quota errors, a table reports "no PRIMARY KEY — refusing to upsert", or verifying Cloudflare DB health.
---

# D1 sync operations

SocialAuto dual-writes to Cloudflare D1 (primary) and PostgreSQL (failover)
via `app/services/db_router.py`; `app/services/db_sync.py` runs the
bidirectional sync. `D1Client` tracks a daily write budget.

## Known failure modes

| Symptom | Cause | Resolution |
|---|---|---|
| `exceeded D1's free tier daily row write limit` | Free tier cap (~100k row writes/day, resets at UTC midnight) | Wait for reset, reduce sync churn, or upgrade the D1 plan. Postgres keeps serving via circuit breaker. |
| `table 'post_targets' still has no PRIMARY KEY — refusing to upsert` | Table created without PK; `INSERT OR REPLACE` then duplicates rows (post_targets once ballooned to 150k+ rows) | `db_sync._repair_d1_pk` auto-recreates the table as `<table>_repaired` with the correct PK and swaps it — but needs write quota to run. Retry `sync` after quota resets. |
| circuit breaker open | 3+ consecutive D1 failures → 60s Postgres-only window | Self-heals; replay queued writes after recovery. |

## Ops tool

```bash
python3 scripts/d1_ops.py health          # d1/kv/vectorize + write budget
python3 scripts/d1_ops.py status          # router state, replay queue depth
python3 scripts/d1_ops.py tables          # D1 tables + row counts
python3 scripts/d1_ops.py pk post_targets # PASS/WARN on PRIMARY KEY presence
python3 scripts/d1_ops.py sync            # full bidirectional sync (uses quota!)
python3 scripts/d1_ops.py replay          # flush queued writes to D1
```

Or raw endpoints:

```bash
curl http://localhost:8083/api/v1/cf-db/health   # includes d1_write_budget
curl -X POST http://localhost:8083/api/v1/cf-db/sync
curl -X POST http://localhost:8083/api/v1/cf-db/replay
curl http://localhost:8083/api/v1/cf-db/tables
```

## Repair procedure for a PK-less table

1. `d1_ops.py health` — confirm the write budget has headroom. If
   exhausted, wait for the UTC-midnight reset; do NOT force syncs.
2. `d1_ops.py pk <table>` — confirm the table lacks a PK.
3. `d1_ops.py sync` — the sync loop detects the missing PK and runs
   `_repair_d1_pk` automatically (recreate → copy → swap). It logs
   `D1 PK repair failed` if quota blocks it mid-way — safe to re-run.
4. `d1_ops.py pk <table>` again → PASS; `d1_ops.py tables` to sanity-check
   row counts vs Postgres.

## Gotchas

- Every `sync` burns write quota proportional to changed rows — don't
  loop it to "watch" progress.
- The quota error is Cloudflare-side, not the app's budget counter —
  `d1_write_budget` is the app's own tracking and may read lower than
  reality if other tools wrote directly.
- Postgres is always the source of truth for repair; D1 rows are a
  copy. Never treat D1 as authoritative after a quota gap — verify with
  `tables` counts.
