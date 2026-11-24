# pr-ci-triage

Triage failing CI on your own PRs fast — distinguish **"test encodes the
old behavior"** (update the test) from **"real regression"** (fix the
code), then push the fix and let auto-merge land it.

## When CI fails on your PR

1. **Digest first** — never read raw logs blindly:

   ```bash
   python3 .devin/skills/pr-ci-triage/scripts/pr_fail_digest.py <PR#>
   python3 .devin/skills/pr-ci-triage/scripts/pr_fail_digest.py --all
   ```

   **Before pushing a model/constant migration**, catch stale assertions
   proactively:

   ```bash
   python3 .devin/skills/pr-ci-triage/scripts/test_const_drift.py
   ```

   Scans `tests/` for `@cf/` IDs that are retired or absent from the live
   Workers AI catalog (reuses `cloudflare-ops/scripts/cf_model_audit.py`).
   Exit 1 on hits — **review each**: explicit `model=` fixtures exercising
   URL/payload mechanics are benign (they never hit the catalog); only
   assertions on *defaults* or removed code paths actually break.

   Prints each failing check + failed test node IDs + first assertion
   lines. `--full` for more context.

2. **Classify each failure:**

   | Signature | Verdict | Fix |
   |---|---|---|
   | `assert X == <old constant>` where you *intentionally* changed X | stale test | update the assertion to the new value |
   | Test exercises a code path you *removed/short-circuited* | stale test | rewrite the test for the new contract (or delete if coverage moved) |
   | Test mocks internals you renamed/re-signed | stale test | update mocks/call args |
   | Assertion fails on *unrelated* behavior you didn't touch | real regression or flake | reproduce locally, check `pr-ci-flake-watch` history |
   | Coverage gate <80% on touched files | real | add tests for new branches |

3. **The intentional-change checklist** (what burned PR #2474):
   - Defaults/constants renamed or retargeted → grep tests for the old
     literal (`rg "llama-3.1-8b" tests/`)
   - Function signature default changed → tests calling without the arg
     now hit a different path; pass the arg explicitly to keep coverage
   - Early-return/short-circuit added → tests mocking the removed path
     must be rewritten, not patched
   - Error-code changes (e.g. 502→503) → update `status_code` asserts

4. **Verify locally before pushing** — run only the touched test files:

   ```bash
   cd social-automation/backend
   .venv/bin/python -m pytest tests/unit/test_<module>.py -q
   .venv/bin/ruff check tests/ && .venv/bin/ruff format tests/
   ```

   Then `git push` — auto-merge re-arms itself; required checks rerun.

## Session PR watch loop

For live watch-and-report inside a session (cron fleet owns the durable
watch — `pr-land-watch`, `pr-ci-fix-watch`, `pr-merge-sweep`):

```bash
# poll every 2min, log transitions, cap 2h
cat > /tmp/pr_watch.sh <<'EOS'
#!/bin/bash
cd ~/cu130-slim; declare -A last
for i in $(seq 1 60); do
  gh pr list --json number,state,mergeStateStatus,statusCheckRollup \
    -q '.[]|[.number,.state,.mergeStateStatus,([.statusCheckRollup[]|select(.conclusion=="FAILURE" or .conclusion=="ACTION_REQUIRED")|.name//.context]|join("|"))]|@tsv' \
  | while IFS=$'\t' read -r n s m f; do
      k="$n:$s:$m:$f"; [ "${last[$n]:-}" != "$k" ] && { echo "$(date +%H:%M) PR $n $s/$m ${f:+FAILS:$f}"; last[$n]="$k"; }
    done
  sleep 120
done
EOS
bash /tmp/pr_watch.sh   # run in background, read output periodically
```

## Notes

- `BLOCKED` mergeStateStatus with checks `QUEUED/IN_PROGRESS` is normal —
  the heavy matrix (image builds + Trivy) is slow. Only `FAILURE`,
  `ACTION_REQUIRED`, `DIRTY`, or `BEHIND` need action.
- Parallel agents also push to open PR branches — `git fetch` and check
  `git log` on the branch before assuming the head is yours.
- Never claim a PR merged without `gh pr view` state — auto-merge may be
  armed but checks still running.

## CodeQL / GHAS tool-status checks (codeql_triage.py)

The `CodeQL`/`Trivy` check runs from the `github-advanced-security` app are
tool-status checks, NOT workflow jobs — they compare the analysis
configurations GitHub recorded on the base ref against what the PR run
produced.

- **"N configuration(s) not found"** alone = neutral noise. Causes seen
  live: the JS extractor resolves `javascript`→`javascript-typescript` and
  registers both extracted languages on master, so a PR uploading only
  `/language:javascript` trips a phantom `/language:typescript` miss; Trivy
  matrix configs that only ran on push (not PR) do the same.
- **The run only flips to FAILURE when the same summary carries
  "New alerts … N critical"** — i.e. the PR introduced a real finding.
  Fix the code, don't chase the config list.

```bash
python3 .devin/skills/pr-ci-triage/scripts/codeql_triage.py 2586
# prints each GHAS run: conclusion, missing configs, new-alert counts,
# then the open CodeQL alerts on refs/pull/<N>/merge (rule, severity,
# file:line). Exit 1 when any run failed.
python3 .devin/skills/pr-ci-triage/scripts/codeql_triage.py 2586 --analyses
python3 .devin/skills/pr-ci-triage/scripts/codeql_triage.py \
    --stale /language:typescript            # find master analyses to delete
python3 .devin/skills/pr-ci-triage/scripts/codeql_triage.py \
    --stale /language:typescript --delete   # delete them (confirm_delete)
```

If a missing config IS backed by a stored master analysis that should no
longer exist (renamed category/workflow), `--stale` + `--delete` clears it;
the check then stops expecting it on PRs.
