# branch-consolidation-ops — merge all branches into production

Lands every remote branch with unique content onto `master` via PRs.
Built from the 10-PR consolidation of 2026-11-03 — the mechanics GitHub
won't do for you:

1. **Uniqueness = `git cherry`, not commit count or 2-dot diff** — squash
   merges change SHAs so `rev-list --count` lies. `git cherry` compares
   patches; `+` commits = genuinely unmerged work. A "43 ahead" branch
   can be fully covered content-wise.
2. **PR per branch** with auto-merge armed.
3. **Bot/healer-pushed runs come back `action_required`** — approve via
   `gh api -X POST repos/{r}/actions/runs/{id}/approve` or required
   checks never run.
4. **`update-branch` API does NOT trigger workflows** — the merge commit
   it creates has zero check-runs, so required checks stay "expected"
   forever and the PR sits BLOCKED. The fix: merge master into the
   branch in a worktree + push an empty retrigger commit — real pushes
   fire `pull_request` synchronize runs.
5. **Strict up-to-date protection = wave loop** — each merge re-behinds
   every other PR. Repeat: update head → push retrigger → checks →
   auto-merge. ~1 PR lands per wave.
6. **Cleanup**: verify branch content is covered before deleting —
   post-merge `git diff master...branch` should be empty-ish; pull out
   orphan files (the mautic-ops SKILL.md rescue, #1284) before force-
   deleting locals.

## Step 0 — census before consolidating (2026-11-13)

"Merge all branches" is meaningless until you know each branch's PR
state. Run the census FIRST:

```bash
python3 scripts/branch_census.py            # report
python3 scripts/branch_census.py --delete-merged   # also drop landed refs
```

It classifies every remote branch: OPEN (feed to pr-sweep/consolidator),
MERGED (already on base — safe to delete), CLOSED (superseded/rejected —
grouped into `-rebuilt` feature families, do NOT blanket-merge), NO-PR.

Session finding (65 branches → 0 open PRs): every CLOSED feature family
had already landed via a canonical "(conflict-free rebuild)" PR under a
different branch name — verify with `git log origin/master --grep` for
the feature keyword before resurrecting anything. `unique_patches`
(git cherry) INFLATES on squash-merged branches — it counts patch-ids,
not landed-ness; the residual `git diff master...branch` on stale
branches shows master's newer work, not branch additions.

Tool: `scripts/branch_consolidator.py [--base master] [--repo o/r]
[--waves N]` — scans, creates PRs, approves runs, runs the update+
retrigger waves, reports stragglers (conflicts are reported, not
auto-resolved — resolve in a worktree, never the bind-mounted checkout).

Gotchas this session: local branch tips ≠ remote tips (`cherry` vs each);
`.devin/BACKLOG.md` conflicts are churn — take master's side; hardcoded
secrets in branch history → flag for rotation.
