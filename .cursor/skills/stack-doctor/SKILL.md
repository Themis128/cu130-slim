# Stack Doctor — recurring failure modes + fixes

Built from the actual failure patterns hit on this stack. Each maps to a
verified fix, not a guess.

## 1. Docker Desktop bind-mount drift

**Symptom:** `docker compose cp` / edits "don't take" — container reads
stale files even though host checkout is current. Hit repeatedly: `leads.py`,
`newsletter_subscribers.py`, `celery_app.py`, `tests/`.

**Root cause:** `/app/app`, `/app/alembic`, `/app/tests` bind-mount from
`C:\Users\tbaltzakis\cu130-slim` via Docker Desktop — the mount can lag
the real checkout (WSL↔Windows sync), and `docker cp` on a mounted dir
writes a second shadow copy (`revision present more than once`).

**Fix:** after a git pull, `docker compose restart <svc>` — the mount
then serves the fresh host file. For alembic, NEVER `docker cp` a
migration into `/app/alembic` — the bind mount makes it a duplicate.
Script: `scripts/check-mount-drift.sh` (diffs host vs container file).

## 2. Stacked-branch PR conflicts

**Symptom:** 10+ PRs all `CONFLICTING` on the same files (SKILL.md,
BACKLOG.md, celery_app.py) because each feature branch stacked on the
previous one's branch, not master.

**Fix:** merge ONE carrier (the newest, which contains the cumulative
diff) then close the identical-diff dupes. Detect: identical
`additions+deletions` across PRs = stacked carriers.
`gh pr view N --json additions,deletions` — same number = same diff.
Then rebase-merge per PR with `-X ours` on code files, union on docs.

## 3. Redis/env password drift

**Symptom:** workers `invalid username-password pair` but `redis-cli -a`
pings. `.env` rotated while Redis kept its `requirepass`.

**Fix:** read the live `requirepass` from `docker inspect redis`, write
it back to `.env`, recreate dependents. `compose-watchdog.sh` automates
this (host cron, alerts on drift).

## 4. k3s unreachable (workflow kubeconfig dead)

**Symptom:** every k3s workflow "succeeds" but does nothing —
`localhost:8080 connection refused`. `KUBECONFIG_B64` points at a proxy
that never exists.

**Fix (landed in #2286):** `scripts/ci/kubectl-via-omv-ssh.sh` — a
`kubectl` shim that SSHes to omv (`tbaltzakis@100.74.191.58`,
`OMV_SSH_KEY`, `sudo k3s kubectl`). Tailnet waits must target `:22`.

## 5. LinkedIn session loss + 2FA

**Symptom:** sidecar `logged_in: False`, cookie transplants get
`ERR_HTTP_RESPONSE_CODE_FAILURE` — LinkedIn scores IP+fingerprint.

**Fix:** manual login in the noVNC browser (`localhost:6080`) — the
fingerprint matches, so the session holds. Then `POST /debug/eval`
in-page `fetch()` works (raw cookie replay 401s). For subscriber data,
DOM-scrape the analytics subscribers dialog — voyager endpoints 400.

## 6. EspoCRM field validation

**Symptom:** `POST /Lead → 400` on `source`/`title`. Espo source must be
an enum value (`Newsletter`, `Web Site`, `Partner`), title ≤ ~90 chars.

**Fix:** validate the field against the enum before POST — see the live
list via `GET /Lead?select=source` on existing rows.

## Scripts

- `check-mount-drift.sh` — diff a host file against its container copy
- `espo-source-check.sh` — print the valid `source` enum values
