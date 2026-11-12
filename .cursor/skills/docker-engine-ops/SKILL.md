---
name: docker-engine-ops
description: >
  Docker daemon topology guard for this host — the stack lives in the
  Docker Desktop (docker-desktop) engine, but a WSL-native docker.service
  can steal /run/docker.sock and answer `docker info` with an empty
  engine (2026-11-12 incident: every recovery lane pulled the whole image
  set into a daemon that hosts nothing while the real stack stayed
  Exited(127)). Covers the docker.exe pinning convention for cron/watch
  scripts, the dd-user-distro-proxy.service that restores bare `docker`,
  and the docker_engine_guard.py verifier. Use when docker commands hit
  the wrong daemon, containers mass-exit 127 after an engine flap, or
  before writing any new script that shells out to docker.
triggers:
  - user
  - model
---

# Docker Engine Ops

## Topology (this host)

- **Real engine**: Docker Desktop (`docker-desktop`), reached two ways:
  - `docker.exe` — absolute path
    `/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe`
    (root cron PATH lacks the Windows interop dirs — always use the
    absolute path, never bare `docker.exe`).
  - bare `docker` — only works while `dd-user-distro-proxy.service`
    (systemd, root) runs `docker-desktop-user-distro proxy`; it binds
    `/var/run/docker.sock` → the DD engine.
- **Enemy**: WSL-native `docker.service`/`docker.socket` —
  **disabled** since 2026-11-12. Socket activation meant the FIRST bare
  `docker` call after boot spawned an empty native daemon that owned
  `/run/docker.sock`. If it ever re-enables, every bare-docker caller
  silently targets nothing.
- **Credential helper**: `docker.exe` resolves
  `docker-credential-desktop.exe` through the child PATH that WSL
  interop derives from the caller's PATH — cron's bare PATH breaks ghcr
  pulls ("executable file not found"). Scripts that pull must
  `export PATH="$PATH:$(dirname "$DOCKER_EXE")"`.

## Rules for new scripts

1. Never call bare `docker`/`docker compose` in cron or watchers — use
   the `DOCKER_EXE` absolute path with `[ -x ] || DOCKER=docker`
   fallback (see `social-stack-watchdog.sh`, `docker-recovery.sh` for
   the canonical block).
2. Add the PATH export above when the script pulls images.
3. Python callers: `DOCKER = DOCKER_EXE if Path(DOCKER_EXE).exists() else "docker"`
   and append the bin dir to `os.environ["PATH"]`.

## Tool: docker_engine_guard.py

`scripts/docker_engine_guard.py` — read-only verifier:

- `docker_engine_guard.py` → per-check OK/FAIL/WARN, exit 1 on FAIL
- `--alert` → TG #ops on FAILs (6h dedupe)
- `--json` → machine-readable

Checks: bare docker → docker-desktop, docker.exe reachable,
docker.service/socket disabled, proxy unit active,
`distro-services/ubuntu-24-04.sock` present (absent = bind-mount
containers will exit 127).

Suggested cron (user): `*/30 * * * * python3 .../docker_engine_guard.py --alert`

## Repair ladder

1. `docker_engine_guard.py` → read the FAIL line.
2. `native-docker-active` → `systemctl disable --now docker.socket docker.service`
   (needs root: `wsl.exe -d Ubuntu-24.04 -u root -e systemctl ...` works
   without a password).
3. `proxy-dead` / bare docker unreachable →
   `systemctl restart dd-user-distro-proxy` (unit file:
   `/etc/systemd/system/dd-user-distro-proxy.service`; it crash-loops
   until DD re-registers `distro-services/ubuntu-24.04.sock`).
4. Engine flapping / distro-services socket missing →
   `docker.exe desktop restart`, then re-check the guard.
5. `engine-down` persisting after restart → read
   `%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log` for the
   `wsl-bootstrap` stderr — the 2026-11-12 blocker was
   `copying wsl distro proxy binary: ... docker-desktop-user-distro:
   text file busy` (ETXTBSY): the proxy unit exec'd the binary straight
   out of `/mnt/wsl/docker-desktop/`, so every DD bootstrap failed
   overwriting it. **Fixed permanently**: the unit now execs a copy at
   `/usr/local/bin/dd-user-distro-proxy` — never point ExecStart back
   into `/mnt/wsl/docker-desktop/`. If ETXTBSY recurs, check for other
   processes holding files under that mount.
6. Everything else → `wsl-docker-doctor` skill (`fix` ladder L1–L4).

## History

- 2026-11-12: Docker Desktop wiped images + native docker.service stole
  the socket → mass Exited(127), wrong-daemon pulls. Fixed by disabling
  native units, pinning all lanes to docker.exe, creating the proxy
  unit, and `docker desktop restart` to restore distro-services.
- 2026-11-12 (same day, second wave): DD engine refused to start —
  wsl-bootstrap ETXTBSY on the proxy binary held open by our own
  `dd-user-distro-proxy.service`. Fix: `systemctl stop` the unit →
  `docker desktop restart` → re-point ExecStart to
  `/usr/local/bin/dd-user-distro-proxy` (a copy) so DD can always
  overwrite the original. Guard caught it live via `engine-down` +
  `distro-services-missing`.
