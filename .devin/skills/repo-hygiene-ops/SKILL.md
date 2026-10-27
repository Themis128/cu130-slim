---
name: repo-hygiene-ops
description: >
  Diagnose and repair a dirty/confused git checkout in this repo: stale
  snapshot vs real WIP classification (hash-compare worktree vs
  origin/master), mode-bit noise (core.fileMode=false), CRLF porcelain
  gotcha, root-owned files that break reset/checkout (fix =
  `sudo chown -R`), the WSL-vs-Windows dual checkout (Docker mounts
  C:\Users\tbaltzakis\cu130-slim, NOT /home/tbaltzakis/cu130-slim), stale
  worktree pruning, and dependabot alert handling. Use when `git status`
  looks wrong, reset fails with "Permission denied", or mass "modified"
  files appear from nowhere.
---

# Repo Hygiene Ops — dirty-tree forensics

Tool: `scripts/repo_doctor.py` — classifies every dirty path, prints the
fixes. Run it before touching an unfamiliar dirty checkout.

## The dual checkout (read first)

Two working copies exist:

- `/home/tbaltzakis/cu130-slim` — WSL-native checkout (this repo's git ops
  happened here; agent sessions default to it).
- `C:\Users\tbaltzakis\cu130-slim` = `/mnt/c/Users/tbaltzakis/cu130-slim` —
  **the one Docker Compose bind-mounts** (`docker inspect social-api` →
  `Source: C:\Users\...\social-automation\backend\app → /app/app`).
  Container file edits/tests see the WINDOWS tree, not the WSL one.

Drift between them is expected; sync WSL→upstream and treat /mnt/c as the
runtime checkout. Don't assume `docker compose cp` targets the files you
see in WSL.

## Diagnostic playbook (what bit us 2026-10-27)

1. **Stale snapshot vs real WIP** — for each ` M` file compare worktree
   blob to upstream: `git hash-object $f` vs `git rev-parse origin/master:$f`.
   All-equal = stale snapshot (safe to `git reset`); differing = real WIP
   (preserve). Untracked-dirs check: `git status --porcelain` prints dirs
   with trailing `/` — expand them before comparing to `git ls-tree -r`.
2. **Mode noise** — files land 777; index tracks 644/755 → hundreds of
   phantom ` M`. Fix once: `git config core.fileMode false`.
3. **CRLF porcelain** — `git status --porcelain` paths carry `\r` on this
   setup; piping them into `git show`/`git checkout` silently fails.
   Always `| tr -d '\r'`.
4. **Root-owned paths** — container/`sudo` runs leave root-owned dirs;
   `unlink`/`checkout`/`reset --hard` fail with "Permission denied" even
   when the file itself is user-writable (dir write perm is what matters).
   Workaround for content restore: `git show HEAD:$f > $f` (truncate-write
   needs only file perm). Real fix: owner runs
   `sudo chown -R tbaltzakis:tbaltzakis <repo>`.
   **A failed `reset --hard` partially rolls files back** — afterwards,
   re-verify each remaining ` M` is a real diff, not reset-inflicted.
5. **Sync HEAD without touching files** — `git reset origin/master`
   (mixed) moves HEAD+index, leaves worktree alone; good when unlinks
   are blocked.
6. **Stale worktrees** — `git worktree list` shows `prunable` entries;
   `git worktree prune` clears them. Leave other sessions' live branches.
7. **Local-only commits** — before "rescuing" an unpushed commit, check
   `git log origin/master -- <its paths>`; it's often already merged under
   a different squash SHA.
8. **Dependabot** — `gh api repos/<o>/<r>/dependabot/alerts` lists open
   alerts with fix versions; for npm transitive bumps:
   `npm update <pkg>` then PR the lockfile.

## Safety

- Never delete uncommitted work without the hash-compare proof it's stale.
- Never `reset --hard` while any dir is root-owned — it half-applies.
