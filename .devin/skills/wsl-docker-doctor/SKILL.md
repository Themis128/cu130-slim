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
