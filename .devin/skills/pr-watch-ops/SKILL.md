# pr-watch-ops — one-command open-PR triage + mechanical unblock

Sweeps every open PR, classifies each red check against the known-noise
signature table, and mechanically fixes what needs no judgment: approves
`action_required` workflow runs, fires `update-branch` on BEHIND PRs, and
arms `--squash --auto` merge where it isn't armed.

Born from the 2026-11-04 CI storm: ~15 manual `gh` round-trips per sweep
collapsed into one call.

## Use it

```bash
# report only — one line per PR + an ACTIONABLE list of real failures
python .devin/skills/pr-watch-ops/scripts/pr_watch.py

# full sweep — report AND fix mechanical blockers
python .devin/skills/pr-watch-ops/scripts/pr_watch.py --fix

# different repo
python .devin/skills/pr-watch-ops/scripts/pr_watch.py --repo Themis128/devoptimizer
```

Requires `gh` authenticated (Windows binary or `wsl gh` via `devin-queue/ghw.cmd`).

## Failure classes (from pr-event-triage, kept in sync)

| Signature | Class | Meaning |
|---|---|---|
| `CodeQL` | advisory | dependency-CVE alerts in scanned metadata + "config not found" drift warnings — **never a merge gate** |
| `Cursor Approval`, `SonarCloud`, `codecov` | advisory | informational bots — skip |
| `Trivy *`, `container-scan`, `Generate SBOM` | scan | infra-flavored: check whether the branch carries the current fix stack before re-diagnosing (2026-11-04: trivy 0.58.1 yanked → 0.76.1; runner disk OOM → ungated cleanup) |
| everything else (`test`, `integration-test`, `validate`, `secret-scan`, `Ruff Lint`, `build-and-push`) | **actionable** | pull the job log |

## Mechanical fixes performed by `--fix`

1. **`action_required` runs** — approved via `POST /actions/runs/{id}/approve`.
   Heal-bot and first-time-contributor runs sit here forever otherwise.
2. **`mergeStateStatus == BEHIND`** — `PUT /pulls/{n}/update-branch`.
   Strict-mode master makes every merge invalidate all others (the treadmill).
3. **`autoMergeRequest == null`** — arms `gh pr merge --squash --delete-branch --auto`.
   Auto-merge survives branch updates and fires the moment gates go green.

## Exit contract

- `0` — no actionable failures (red present is all advisory/scan noise)
- `1` — actionable failures listed; pull those job logs

## Gotchas learned (2026-11-04)

- `gh pr checks` shows the LAST run per check name — after `update-branch`,
  new runs appear within ~1 min; a `pass:1 pend:0` board means "retriggering",
  not "done".
- GitHub's backend throws phantom `commit_refs`/`502` errors during push storms —
  pushes often land server-side anyway; always `git fetch` + verify before retrying.
- `update-branch` 422 "expected head sha didn't match" = another update or a
  force-push in flight — retry once, then leave it (owner-bots self-heal).
- The `useAuth`/`useTheme` provider errors in `integration-test` logs are
  tolerated frontend noise — the real failing step is usually
  "Run backend unit tests"; grep the step list, not the log tail.
- Another agent may rebase a PR branch and drop your pushed fix — verify your
  commit is still an ancestor (`git merge-base --is-ancestor <sha> origin/<branch>`)
  before re-diagnosing "the same" failure.
