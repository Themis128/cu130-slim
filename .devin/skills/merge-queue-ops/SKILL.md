# merge-queue-ops

## ⚠️ UNAVAILABLE on this repo (verified 2026-11-22)

GitHub gates merge queues to **organization-owned** public repositories.
`Themis128/cu130-slim` is **user-owned** — the rulesets API rejects the
`merge_queue` rule type with HTTP 422, and GraphQL has no
`mergeQueueParameters` input. `merge_queue.py enable` refuses early via an
owner-type preflight. The only way to enable it for real is transferring
the repo to an org.

What replaced the queue's role here:
- `strict: false` on classic protection — BEHIND is cosmetic, armed
  auto-merge fires the moment the 5 required checks pass, no
  update-branch needed.
- `.github/workflows/pr-autopilot.yml` (#2483) — event-driven
  arm-on-open + merge-on-green; the gap a queue would have filled.

The rest of this doc is preserved for the day the repo moves to an org.

---

## Required-check map (branch protection)

| Check context | Workflow file | Job |
|---|---|---|
| `validate` | docker-compose-validation.yml | `validate` |
| `secret-scan` | secret-scan.yml | `secret-scan` |
| `ruff` | ruff-lint.yml | `ruff` |
| `test` | social-automation-backend.yml | `test` |
| `integration-test` | integration-tests.yml | `integration-test` |

ALL five workflows must have a `merge_group:` trigger on master BEFORE the
queue is enabled, or queued merges stall with "Expected" checks forever.
Readiness PR: #2080 (`chore/merge-queue-readiness`).

## Tool

```bash
python3 .devin/skills/merge-queue-ops/scripts/merge_queue.py status   # current state
python3 .devin/skills/merge-queue-ops/scripts/merge_queue.py enable   # flip protection + ruleset
python3 .devin/skills/merge-queue-ops/scripts/merge_queue.py disable  # rollback
python3 .devin/skills/merge-queue-ops/scripts/merge_queue.py verify   # enqueue smoke check
```

`enable` is idempotent and does, in order:
1. Verifies all 5 required workflows on origin/master contain `merge_group:`
   (refuses otherwise — enabling early deadlocks the queue).
2. PATCHes classic branch protection `required_status_checks.strict` → `false`
   (queue handles freshness; strict would defeat the purpose).
3. Creates ruleset `master-merge-queue` (target `refs/heads/master`,
   enforcement `active`): `merge_queue` rule (squash, ALLGREEN,
   max_entries_to_build/merge 5, min_entries_to_merge_wait 5min,
   check_response_timeout 60min — exact REST schema per GitHub rulesets
   docs) + `required_status_checks` rule with the same 5 contexts.

`disable` deletes the ruleset and restores `strict: true`.

## Semantics after enable

- Auto-merge still works: eligible PRs are added to the queue automatically
  (`gh pr merge --auto --squash` arms it; the queue does the rest).
- `mergeStateStatus` may show `QUEUED`/`AWAITING_CHECKS` — normal.
- The `changes` gates inside backend/integration workflows fail open on
  `merge_group` (event_name != pull_request → run full suite). Verified
  2026-11-16; do not "optimize" them to skip on merge_group.
- Non-required workflows without `merge_group:` simply don't run on queue
  refs — they don't block merges.
- `enforce_admins: true` stays: admins are bound by the queue too.

## Rollback

`merge_queue.py disable` fully restores prior behavior. If the queue
stalls (checks "Expected" on `gh-readonly-queue/*` refs), check that
origin/master workflows actually contain `merge_group:` — a stale master
is the #1 cause.

## References

- GitHub docs: Managing a merge queue
- github.blog: "How GitHub uses merge queue to ship hundreds of changes"
- MergeRamp/Mergify guides: merge queue vs auto-merge trade-offs
