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

Two trees exist (post wsl-distro-migrate, verified 2026-11-18):

- `/home/tbaltzakis/cu130-slim` — **the live checkout.** The compose
  project working_dir IS this path; containers bind-mount it
  (`docker inspect social-api` → `Source: /home/tbaltzakis/cu130-slim/...`).
  A `git pull`/checkout here IS the container file sync — no `docker cp`.
- `C:\Users\tbaltzakis\cu130-slim` = `/mnt/c/Users/tbaltzakis/cu130-slim` —
  **no longer the runtime checkout.** It survives only as (a) the
  `stack-watchers` repo mirror mounted at `/app:ro` by
  `docker-compose.override.yml`, and (b) `.docker-bin/` (docker CLI +
  cli-plugins) for that container. It is intentionally partial — only the
  five watcher skill dirs (`cdp-watch-ops`, `fb-reauth-ops`,
  `li-vetting-watch`, `pending-triggers-ops`, `wa-verify-ops`) plus
  `.devin`-adjacent files the heal_supervisor spawns. Do NOT expect the
  backend code there; `social-automation/backend/app` does not exist on C:.

Drift that matters: when a watcher skill's scripts change in WSL, mirror
the changed files into the C: copy or stack-watchers keeps running stale
code (fsbridge bypass workaround — see override comments). Verified drift
2026-11-18: `fb-reauth-ops` was stale on C: (single-CDP-lane fb_reauth.py)
while the container ran it — resynced WSL→C: and restarted stack-watchers.

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
