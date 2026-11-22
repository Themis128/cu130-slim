# ci-triage-ops

Queue-level CI triage for cu130-slim — the facts that decide *who* fixes a
failing check and whether the auto-heal lane will beat you to it.

Companion to `pr-ci-triage` (per-failure digest + stale-test-vs-regression
classification). This skill covers the layer above it.

## Tool

```bash
python3 .devin/skills/ci-triage-ops/scripts/ci_fails.py <pr>      # FAILED lines from failing required checks
python3 .devin/skills/ci-triage-ops/scripts/ci_fails.py --healer  # is gemini-ci-fix alive or quota-dead?
```

`ci_fails.py <pr>` is the quick pass — deduped FAILED/error lines per
failing required check. For deeper extraction (test node IDs, assertion
context) use `pr-ci-triage/scripts/pr_fail_digest.py <pr>`.

`--healer` reports the `gemini-ci-fix` lane state — `QUOTA-DEAD` when a
`TerminalQuotaError` comment exists on any open PR (the self-heal bot hit
its daily Gemini quota). When dead, **do not wait for it** — fix manually.
Seen 2026-11-22: quota exhausted mid-sweep, all remaining heals manual.

## Facts that change the triage

- **`test` and `integration-test` run the same backend unit suite** — one
  code/test fix heals both jobs. Don't diagnose them separately.
- **Check the branch head before "fixing" a stale CI log.** Parallel
  agents push fast; the failed job may be on an outdated commit. Verify
  the job's `headSha` vs the PR's current head, or run the failing tests
  locally against the branch tip (happened 2026-11-22 on #2474: commit
  f7508bfc had already healed all 8 failures before the sweep saw them).
- **`strict: false`** — BLOCKED means "required checks pending", never
  "branch behind". update-branch churn is cosmetic; armed auto-merge
  fires the moment the 5 required checks pass.
- **pr-autopilot (#2483) is live** — PRs self-arm on open and merge on
  check-completion event. The 15-min `auto-merge` + 2×/hr `pr-sweep`
  workflows are backstops, not the primary path.
- **Native merge queue is impossible here** — user-owned repo, GitHub
  gates it to org-owned. `merge-queue-ops` carries the verified gate;
  don't retry the rulesets API.

## Ownership rule

- Mechanical fixes (stale model-ID assertions, dead code after `return`,
  unused imports) → commit to the PR branch directly, even on another
  agent's PR — the classification table lives in `pr-ci-triage`.
- Substantive work (new feature behavior, real coverage suites) →
  `gh pr comment` with the diagnosis for the PR owner.
- Never take over IG login/session/recovery work owned by the dedicated
  agent — comment, don't push.

## Worktree discipline

- Prefer `~/.worktrees/<name>` — `/tmp` worktrees get cleaned between
  commands mid-session (happened 2026-11-22).
- If the branch moved since the failing run, `git fetch` + re-check test
  names — migration commits rename/remove stale tests.
- Never build fixes in the shared `~/cu130-slim` checkout — parallel
  agents and watchers churn it.
