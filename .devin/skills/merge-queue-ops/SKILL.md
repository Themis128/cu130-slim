# merge-queue-ops

Enable, verify, and roll back GitHub **merge queue** on `master` for
Themis128/cu130-slim — the canonical fix for the strict-protection merge
treadmill (`strict: true` + 5 required checks invalidates every open PR on
each merge).

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
   enforcement `active`): `merge_queue` rule (squash, build_concurrency 5,
   grouping_strategy ALLGREEN) + `required_status_checks` rule with the
   same 5 contexts.

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
