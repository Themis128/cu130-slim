---
name: docker-desktop-upgrade-ops
description: Upgrade Docker Desktop + audit the WSL2 vsock/fsbridge layer on the Windows host. Use when bind mounts materialize empty, containers mass-Exit(127), `wsl.exe -d docker-desktop` times out (UtilAcceptVsock/WSAETIMEDOUT), or when checking whether WSL/Docker Desktop/engine versions are current. Companion to wsl-fsbridge-ops (diagnosis) and docker-engine-ops (wrong-daemon guard).
---

# Docker Desktop / WSL2 upgrade ops

The stack's bind mounts (`/app/app`, `/app/alembic`, `/app/scripts`, …) reach
containers through Docker Desktop's fsbridge: hashed dirs under
`/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts/...` on the WSL side.
When the `dd-user-distro-proxy` / vsock channel wedges, **new bind mounts
materialize as empty directories** → alembic `Can't find Python file env.py`,
promtail "no client config", mass `Exited(127)`. Engine version is rarely
the cause — the vsock/fsbridge layer is.

## Version ladder (check in this order)

1. **WSL** — `wsl.exe --version`. Already latest ⇒ nothing to do; `wsl --update`
   said "already installed" on 2026-11-15 at 3.0.5.0 / kernel 6.18.58.1.
2. **Docker Desktop** — per-user install at
   `C:\Users\<user>\AppData\Local\Programs\DockerDesktop\frontend\Docker Desktop.exe`.
   `winget upgrade` does NOT see per-user installs; use the versioned
   installer URL + SHA256 from `winget show --id Docker.DockerDesktop --version <v>`.
3. **Engine** — follows DD (`docker version`). 29.9.3 ships with DD 4.99.0.

## Tool

```bash
python3 .devin/skills/docker-desktop-upgrade-ops/scripts/dd_upgrade.py status   # versions + fsbridge probe
python3 .devin/skills/docker-desktop-upgrade-ops/scripts/dd_upgrade.py check    # exit 1 if upgrade/fsbridge bad
python3 .devin/skills/docker-desktop-upgrade-ops/scripts/dd_upgrade.py download # fetch+SHA256-verify installer
python3 .devin/skills/docker-desktop-upgrade-ops/scripts/dd_upgrade.py install  # launch installer on Windows
python3 .devin/skills/docker-desktop-upgrade-ops/scripts/dd_upgrade.py heal     # wsl --terminate docker-desktop
```

## Upgrade procedure (verified 2026-11-15)

1. `dd_upgrade.py check` — confirm target version via `winget show`.
2. `dd_upgrade.py download` — versioned URL only; the unversioned
   `/win/main/amd64/Docker%20Desktop%20Installer.exe` can serve a stale or
   unsigned artifact (got `NotSigned` + ERROR_BAD_EXE_FORMAT once).
   Always verify SHA256 against `winget show` output.
3. Copy to `C:\Users\tbaltzakis\AppData\Local\Temp\` if still in WSL `/tmp`.
4. `dd_upgrade.py install` — `Start-Process ... install --quiet`. DD quits,
   upgrades, relaunches; engine bounces ~2-5 min.
5. After engine returns: `docker compose up -d --force-recreate` the
   crash-loopers so bind mounts rematerialize, then verify `/health`.

## Lighter heal before a full upgrade

`dd_upgrade.py heal` = `wsl.exe --terminate docker-desktop` — restarts ONLY
the docker-desktop distro; Ubuntu-24.04 (this session) survives. DD backend
restarts automatically. If `wsl.exe` itself times out (vsock fully wedged),
the durable heal is `wsl --shutdown` + Docker Desktop restart — kills all
WSL sessions including the agent; watchdogs (`compose-watchdog`,
`social-stack-watchdog`) recover the stack.

## Gotchas

- `powershell.exe`/`wsl.exe` interop flaps DURING vsock wedges — retry;
  a failed `Start-Process` may be transient, not a bad file.
- Deploy lock: check `.deploy.lock` before compose operations; a fresh lock
  means another agent is mid-deploy — wait.
- DD 4.99.0 release notes worth knowing: engine 29.9.3, compose v5.7.0,
  abrupt-shutdown corruption fixes, WSL 3.0.x error parsing, per-user install
  fixes, AF_VSOCK CVE-2026-112917 note (needs WSL ≥3.0.3 — we have 3.0.5.0).
- Never run the installer from WSL `/tmp` directly — copy to `/mnt/c/...`
  first so the Windows path is valid.
