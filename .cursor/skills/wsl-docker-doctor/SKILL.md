---
name: wsl-docker-doctor
description: >
  Diagnose + repair the Docker Desktop WSL integration on this host —
  the failure classes hit 2026-11-02: dead docker-desktop-user-distro
  proxy (in-session docker works, fresh `wsl.exe --exec docker info`
  fails — the watchdog probe), vsock degradation (UtilAcceptVsock /
  WSAETIMEDOUT, coin-toss calls, durable fix `wsl --shutdown`), alembic
  bind-mount drift (stale checkout mounted at /app/alembic lacks
  DB-stamped revisions -> api crash-loop), and hash-prefixed container
  names after a recreate removal race. Companion to windows-host-ops.
---

# WSL Docker Doctor

Tool: `scripts/wsl_docker_doctor.py` (run inside WSL; Python only).

- `status` — probes in-session docker, fresh `wsl --exec` session
  (the watchdog's probe — catches proxy-dead), proxy process, interop,
  watchdog state file. Prints a DIAGNOSIS line per class.
- `fix` — repair ladder: L1 respawn user-distro proxy in place →
  L2 `wsl --terminate` (watchdog's repair) → L3 `docker desktop restart`
  → L4 `wsl --shutdown` (needs `--yes`; kills all WSL sessions).
  Telegram ops alert before/after each disruptive step.
- `alembic` — diffs the `/app/alembic` bind-mount's versions dir vs the
  DB `alembic_version` stamp; lists missing migration files to copy.
- `containers` — renames `<12-hex>_<svc>` containers back to canonical.

Key facts: the Windows watchdog at
`C:\Users\tbaltzakis\scripts\Watch-DockerWslIntegration.ps1` handles the
same classes on a 5-min schedule with Telegram alerts — prefer letting
it run; use this tool for diagnosis and the L4 step it deliberately
won't take.


## IDE-stability layer (2026-11-03 — Devin/Zcode sessions)

Failure classes fixed: `UtilAcceptVsock accept4 failed 110` (vsock decay →
dead interop + dead docker socket), VM idle-shutdown killing IDE sessions,
vmmemWSL pinning starving the host.

- **`.wslconfig` (current, verified 2026-11-11)**: `vmIdleTimeout=-1`,
  `autoMemoryReclaim=gradual` — the 2026-11-07 stability fix REVERTED the
  older dropCache choice (dropCache correlated with VM crashes; gradual is
  the documented stable default — do NOT flip back to dropCache). Current
  sizing: memory=12GB / swap=32GB / processors=8. CommitLimit =
  mem*0.5+swap = 38GB must stay above observed Committed_AS (~34GB);
  2026-11-11 raised swap 24→32GB after Committed_AS 33.5GB exceeded the
  30GB limit during a 6-flap evening → guest overcommit → vsock wedge →
  docker drops. Swap is sparse-VHD backed, no host RAM cost.
  **Do not enable `networkingMode=mirrored`** — tested 2026-09-26, broke
  Docker Desktop host port-forwarding (backup-20260926-mirrored).
- **`docker-socket-watch` Scheduled Task (Windows, every 2 min)**:
  `wsl -d Ubuntu-24.04 -e docker info || wsl -t docker-desktop` — surgical
  recycle restores the socket in ~45s WITHOUT killing the Ubuntu/IDE
  distro. Log: `%USERPROFILE%\docker-socket-watch.log`.
- **`wsl-recycle-watch` Scheduled Task (every 5 min)**: fires
  `wsl --shutdown` when `%USERPROFILE%\wsl-shutdown.flag` exists — the
  full-recycle path when surgical isn't enough (`manual-gates-ops` tool
  `wsl_recycle.py` drops the flag + tries schtasks directly).
- Surgical vs full: `wsl -t docker-desktop` first (IDE survives); full
  shutdown only via the flag watcher — never from inside WSL mid-session.
- WSL 3.0.4.0 = latest (wsl --update no-op) — vsock flakiness is the
  residual known-flaky layer; config + self-heal is the mitigation.
- Docker settings-store: IntegratedWslDistros must include Ubuntu-24.04
  (verified); AutoStart + WslEngineEnabled on.
