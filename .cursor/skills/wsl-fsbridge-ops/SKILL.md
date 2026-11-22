---
name: wsl-fsbridge-ops
description: >
  Diagnose + repair the Docker Desktop WSL fs-bridge failure class first
  seen 2026-11-13: the dd-user-distro-proxy dies on a vsock flap, and from
  then on every NEW bind-mount whose source is
  \\wsl.localhost\Ubuntu-24.04\... materializes inside containers as an
  EMPTY DIRECTORY (I/O error on read) — crash-loops like promtail
  ("at least one client config must be provided") and social-cloudflared
  (/entrypoint.sh unreadable). Detection via proxy pgrep + empty hashed
  mount dirs; durable heal is only a full wsl --shutdown. Companion to
  wsl-docker-doctor (which covers the socket/interop classes).
---

# WSL fs-bridge ops

Tool: `scripts/fsbridge_doctor.py` (run inside WSL; Python only).

- `status` — proxy process, hashed bind-mount count + empty count,
  containers whose bind source is a hashed mount and are restart-looping.
  Prints a DIAGNOSIS line per class.
- `probe` — probe-mount a file from the distro into a throwaway alpine
  container and try to read it — the definitive "is a fresh UNC mount
  poisoned" test.
- `casualties` — list containers restart-looping whose mounts point at
  hashed paths (the ones that need --force-recreate after heal).

## The failure class (mechanism)

Docker Desktop keeps a per-distro agent running inside Ubuntu:
`dd-user-distro-proxy proxy --distro-name Ubuntu-24.04 --docker-desktop-root
/mnt/wsl/docker-desktop <resources>` (pushed by DD via
`wsl.exe -d <distro> -u root -e /run/docker-desktop/docker-desktop-user-distro ...`).

For every container bind-mount whose source is `\\wsl.localhost\<distro>\path`,
the agent creates a TRANSIENT systemd mount unit inside the distro:
`/mnt/wsl/docker-desktop-bind-mounts/<distro>/<sha256>` — these carry no
unit-file parameters; the agent alone knows the source.

Failure sequence (observed 2026-11-13, ~3 recurrences in one evening):
1. vsock exec channel wedges (`Wsl/Service/WSAETIMEDOUT`, ~50% of wsl.exe calls fail)
2. The proxy exits (its own vsock link to DD drops) → DD pops
   "WSL integration with distro 'Ubuntu-24.04' unexpectedly stopped"
3. Existing hashed mounts keep working for RUNNING containers (open handles
   survive); socket + shared-sockets get re-published by DD retries
4. But every NEW mount of a `\\wsl.localhost` source now resolves to an
   **empty directory** — `wc -l` → "Is a directory", `head` → "I/O error",
   `ls -la` shows the dir stamped at the failure instant (21:14 UTC pattern)

## What does NOT fix it (all tried)

- `wsl -t docker-desktop` — engine restarts, mounts stay poisoned
- Full Docker Desktop process restart — same
- `IntegratedWslDistros` toggle off→on in
  `%APPDATA%\Docker\settings-store.json` — engine re-inits, mounts stay poisoned
  (re-pushes the proxy though — needed for the eventual heal)
- `systemctl restart 'mnt-wsl-docker*bind*mounts*.mount'` inside the distro —
  DANGEROUS: transient units report "No mount parameters to operate on",
  unmount successfully, then fail to remount — the mount points are gone
  until the agent re-issues them. Never restart these units; they're
  agent-owned.


## Auto-retire of transplant overrides (2026-11-15+)

Flap #4 response transplanted six services onto named volumes via
`docker-compose.override.yml` (gitignored). Once the bridge heals,
`scripts/fsbridge-reconcile.sh` — called every 5 min from
`social-stack-watchdog.sh` — probes a fresh mount (throttled 30 min),
strips the transplant blocks preserving other overrides, recreates the
services on native mounts with backup+rollback, removes the scratch
volumes, and TG-alerts #ops. Log: `~/logs/fsbridge-reconcile.log`.

## What heals it

A **full `wsl --shutdown`** — resets the whole hvs/vsock plane; on
Ubuntu boot DD re-pushes the proxy, which re-declares every hashed mount,
and restart policies bring containers up with working mounts (verified:
promtail + cloudflared recovered on proxy respawn at 23:54 after the
proxy came back during a channel window).

On this host the heal is already scheduled:
- closing Devin → `vhdx-compact-on-devin-exit` fires within ~5 min (full WSL stop)
- `Nightly-Vhdx-Compact` 05:00 daily

Manual alternative: **hashed-mount rebind** heals individual dead
mounts without a shutdown — see section below (verified 2026-11-20).

The Windows watchdog `DevOptimizer-WSL-Repair` (every 30 min) now:
- logs `[fsbridge] DISTRO PROXY DEAD` + empty-mount count instead of a
  misleading "OK"
- auto re-enables `IntegratedWslDistros` the first pass the distro answers
  `wsl.exe` again (the setting may be hand-disabled to stop popup spam)

## Stopping the popup spam while wedged

DD re-tries the proxy push every failure and re-pops the dialog. Setting
`IntegratedWslDistros: []` in `settings-store.json` silences it — the
watchdog's auto-restore puts it back when the channel recovers. Clicking
"Restart it" during a wedge is harmless but usually futile (the push itself
is a `wsl.exe` call into the same dead channel); it DID succeed twice on
2026-11-13 during brief channel windows, so it's a valid transient reprieve.

## Interim workaround — named-volume transplant

For a critical single-file mount (config, entrypoint) that can't wait for
the heal:

1. Copy the file to a Windows-readable path or seed via `docker run`.
2. `docker volume create <svc>-cfg`
3. Seed: `docker run --rm -v <svc>-cfg:/dst -v C:\path\copy:/src:ro alpine cp /src/<file> /dst/<file>`
4. Recreate the container with `-v <svc>-cfg:/target-parent` (compose
   `docker-compose.override.yml` volume override keeps it managed).
   `C:\` mounts are unaffected by this failure class.

## Working while the exec channel is dead

The 9p file channel (`\\wsl.localhost\<distro>` from Windows) stays alive
independently of the vsock exec channel — use it to:

- read/write files (copy staged scripts into the distro)
- run work via detached scripts: write `script.sh` + `outfile` over 9p, fire
  `wsl.exe -d <distro> -u root -e bash script.sh` in a retry loop, then read
  the outfile over 9p. Immune to mid-run vsock drops.

Renames over 9p work (`Rename-Item`); POSIX symlinks CANNOT be created over
9p (LX_SYMLINK reparse is WSL-internal; `New-Item -ItemType SymbolicLink`
rejects POSIX targets, `mklink` can't store them) — wait for an exec window.

## Manual hashed-mount rebind (heals WITHOUT wsl --shutdown)

Verified 2026-11-20: the dead hashed mount points are STALE `/dev/sdd`
(the Ubuntu VHDX) mounts inside the docker-desktop VM — the disk itself
still mounts clean. You can rebind every dead source by hand:

1. `wsl.exe -d docker-desktop -u root -e sh` — exec into the VM (the
   `wsl: Failed to translate \\wsl.localhost\Ubuntu-24.04\...` warning
   is cosmetic; the distro shell itself works).
2. `mkdir -p /tmp/ubnt && mount /dev/sdd /tmp/ubnt` — a FRESH mount of the
   Ubuntu disk with real content.
3. For each dead hashed dir under
   `/mnt/host/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/<sha256(src)>`:
   - DIR source: `umount -l <hash>; mount --bind /tmp/ubnt<src> <hash>`
   - FILE source: `umount -l <hash>; rm -rf <hash>; touch <hash>;`
     `mount --bind /tmp/ubnt<src> <hash>`
   - `/run/docker.sock` source: same as file, but bind
     `/run/guest-services/docker.container-proxy.sock` (the real
     container-facing engine socket in the VM).
   - `/mnt/c/...` sources: bind from `/mnt/host/c/...` (the C:\ 9p mount
     is unaffected by the Ubuntu bridge poison).
4. `docker start` the previously-Exited(127) containers — mounts now
   resolve to populated content.

Hash = `sha256(source-path)` — enumerate with
`docker inspect <c> --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\n"}}{{end}}{{end}}'`.

Caveats:
- Repairs are per-source (one hash serves every container mounting it).
- A container whose .Source is itself a hashed dir (double-hash) needs
  its outer hash rebound too.
- `docker compose up -d` recreate is still preferable when it works —
  redeclare beats rebind. Rebind is for when mounts can't be recreated.
- Survives until the next engine/VHDX event; the nightly `wsl --shutdown`
  still resets everything to the canonical path.

## Orphan hashed-mount cleanup (verified 2026-11-20)

Empty hashed dirs that NO container references are stale `/dev/sdd`
bind-mount records that survive the bridge heal — `rmdir` fails with
`Device or resource busy` until the mountpoint itself is released.
From inside Ubuntu-24.04 (no docker-desktop exec needed), as root
(`wsl.exe -d Ubuntu-24.04 -u root`):

```sh
cd /mnt/wsl/docker-desktop-bind-mounts/<distro>
for h in */; do
  [ -z "$(ls -A "$h" 2>/dev/null)" ] || continue          # skip non-empty
  refs=$(docker ps -aq | xargs -r docker inspect \
    --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' | grep -c "${h%/}")
  [ "$refs" = "0" ] && umount "${h%/}" && rmdir "${h%/}"
done
```

Only unmount hashes with zero container refs — an in-use empty mount is a
casualty (fix via recreate/rebind above), not garbage.

## Related

- `wsl-docker-doctor` — socket/interop/proxy-respawn classes, `.wslconfig` tuning
- `docker-engine-ops` — docker.exe pinning, wrong-daemon class
- DevOptimizer repo: `scripts/repair-wsl-docker-integration.ps1` (watchdog),
  `logs/nightly-wsl-vhdx-compact.ps1` (scheduled heal)
