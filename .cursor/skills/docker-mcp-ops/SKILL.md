---
name: docker-mcp-ops
description: >
  Share the Docker MCP Toolkit (profiles, catalog, features, gateway)
  between Windows Docker Desktop and Ubuntu-24.04 WSL2. The problem:
  `docker mcp` runs in WSL but sees an EMPTY profile list because WSL has
  its own ~/.docker/mcp/mcp-toolkit.db. Fix: symlink WSL's mcp dir to the
  Windows one (/mnt/c/Users/tbaltzakis/.docker/mcp) — same pattern the
  host already uses for contexts + features.json. Covers verification
  commands, which subcommands can't work in WSL (oauth, secret), and the
  9p-channel staging trick for operating while vsock is wedged.
---

# Docker MCP ops — WSL↔Windows sharing

Tool: `scripts/mcp_wsl_share.sh` (idempotent; run inside Ubuntu-24.04).

## Topology (verified 2026-11-13)

| Piece | Windows | WSL Ubuntu-24.04 |
|---|---|---|
| CLI plugin | `C:\Users\tbaltzakis\.docker\cli-plugins\docker-mcp.exe` | auto-proxied by DD integration — `docker mcp` just works |
| State db | `C:\Users\tbaltzakis\.docker\mcp\mcp-toolkit.db` (~14.3 MB) | `~/.docker/mcp` → **symlink** → `/mnt/c/Users/tbaltzakis/.docker/mcp` |
| Other shared config | — | `~/.docker/contexts`, `~/.docker/features.json` already symlink to the same Windows dir |
| Profiles | `github`, `all_catalog` | same after the link (verified: `profile ls`, `profile show github`) |

Backups taken at link time (2026-11-13): `~/.docker/mcp.bak-20261113`
(original 4.9MB WSL db — rollback path) and `~/.docker/mcp.copy-20261113`.

## Subcommand reachability from WSL

Works: `profile ls/show`, `catalog ls`, `feature ls`, `tools ls`
(8 gateway meta-tools: code-mode, mcp-activate-profile, mcp-add,
mcp-config-set, mcp-create-profile, mcp-exec, mcp-find, ...), `version`,
`gateway run`.

Windows-only (no WSL path):
- `oauth ls` — needs `~/.docker/desktop/tools.sock`; DD only shares
  `backend.sock` + `httpproxy.sock` into integrated distros
- `secret *` — needs the secrets-engine socket
  (`~/.cache/docker-secrets-engine/engine.sock` →
  `/mnt/wsl/docker-desktop/shared-sockets/host-services/secrets-engine.sock`).
  The symlink exists but the engine endpoint was refusing connections
  during the 2026-11-13 flap — retest once the fs bridge is stable.

Caveat: `all_catalog` shows in `profile ls` but `profile show` reports
"not found" — stale/inconsistent profile metadata in the shared db,
not a sharing bug.

## Operating while the vsock exec channel is wedged

`wsl.exe` calls coin-toss fail (`Wsl/Service/WSAETIMEDOUT`) but the 9p
file channel stays up. Pattern that worked 2026-11-13:

- Stage files into the distro via `//wsl.localhost/Ubuntu-24.04/home/...`
  writes (PowerShell `Copy-Item`/`Set-Content` — NOT MSYS `mkdir`/`cp`
  one-liners; path-conversion eats them).
- Rename via 9p `Rename-Item` — worked fine.
- POSIX symlinks cannot be created over 9p (LX_SYMLINK reparse is
  WSL-internal; `New-Item -ItemType SymbolicLink` and `mklink` both fail
  on POSIX targets) — stage a `bash` script and fire it the next time an
  exec window opens; write results to an outfile and read it over 9p.
- Copy the Windows db over 9p first if you need profiles immediately;
  convert to a symlink later for live sharing.

## Verification battery (run inside WSL)

```bash
docker mcp profile ls            # github + all_catalog
docker mcp profile show github   # full server spec incl. pinned image digest
docker mcp feature ls            # mcp-oauth-dcr enabled, oauth-interceptor disabled
docker mcp tools ls              # 8 gateway meta-tools
docker mcp version               # v0.44.3 (2026-11-13)
```

## Rolling back

```bash
cd ~/.docker && rm mcp && mv mcp.bak-20261113 mcp   # restores the 4.9MB WSL-native db
```
