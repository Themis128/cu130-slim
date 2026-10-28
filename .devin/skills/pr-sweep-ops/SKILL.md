# pr-sweep-ops — sweep all open PRs onto protected master

Use when asked to "merge all PRs", "address all PRs", or clear a PR backlog on cu130-slim.

## Protection facts (verified 2026-10-27)

- `master` is protected, strict mode: branches must be up-to-date AND required checks green (`validate`, `secret-scan`).
- Auto-merge is repo-enabled: `gh pr merge <N> --squash --auto` lands a PR when checks pass — but every master merge re-invalidates all other open PRs (the "treadmill").
- `--admin` does NOT bypass required checks.
- No required_conversation_resolution on master (unlike cloudless.gr/main).
- A parallel merger may be active — always re-query `gh pr list` before claiming state; PRs appear/vanish mid-session.

## Strategy ladder (pick per branch)

1. **Clean + green** → `gh pr merge --squash` directly.
2. **BEHIND, no conflicts** → `gh api -X PUT repos/Themis128/cu130-slim/pulls/N/update-branch`, wait for the 2 required checks, merge.
3. **Conflicted, linear history** → worktree rebase onto origin/master, resolve, force-push.
4. **Merge-commit-heavy branch** → DON'T rebase. Fresh branch off master + `git merge --squash <head>` + single commit + force-push to the PR branch.
5. **Many branches** → **umbrella PR**: branch off master, real `git merge --no-ff <each head>`, resolve each once, open ONE PR. When it lands via merge commit, every constituent head SHA is in master history → GitHub auto-marks those PRs merged. Subset/stacked PRs left open afterwards get closed as superseded.

## Worktree discipline

- The main checkout is usually dirty — never work there: `git worktree add /tmp/pr-work origin/master --detach`.
- If a branch is checked out elsewhere (`/tmp/wt-*`), don't clobber it.
- Commit each squash before moving to the next branch — staged files bleed into the next merge otherwise.

## Conflict-resolution rules learned

- Engagement service/task conflicts: preserve the ping-once behavior (rows drafted THIS sweep only) — the old "re-query all pending_human_gate within 2h" re-sends `<!channel>` every sweep. Ours (master) wins.
- `meta_data: dict | None = None` convention on `EngagementCandidate` — keep master's, plus `or {}` safety at call sites.
- Alembic multi-head PRs: after merge, verify single head — script the revision/down_revision graph (including tuple-style `down_revision = ("a","b")`); check for dangling refs.
- `.devin/BACKLOG.md` conflicts: union-merge both sides' entries, newest on top.

## Gate

- Inside `social-api`: touched-module pytest only (1 GiB mem_limit OOMs the full suite); full suite is CI's job or host `.venv`.
- `ruff check` clean on changed backend files; `docker compose config --quiet` valid.
