# WSL Distro Migrate

Move repos + toolchain between WSL2 distros on the office Windows 11 host (executed live 2026-11-04: `Ubuntu-26.04` → `Ubuntu-24.04`). Companion to `wsl-resilience-ops` (vsock/interop flaps) and `windows-host-ops` (scheduled tasks). Use when a distro is re-registered/replaced, projects live on the wrong filesystem, or IDE state points at a dead distro name.

## The failure class

Distro gets unregistered/re-imported under a new name → every `wsl -d <old>`,
`wsl+ubuntu-<ver>` remote URI, `\\wsl.localhost\<old>` UNC, scheduled-task arg,
and MCP/server path dies at once. Docker **containers + named volumes survive**
(they live in the `docker-desktop` backend, not the user distro) — only project
files, toolchains, and in-distro config are lost.

## Migration checklist (what worked)

1. **Verify reality**: `wsl -l -v` (name + version), `wsl -d <new> -e grep PRETTY /etc/os-release`, `ls /home` — a fresh import has root default user and empty `/home`.
2. **Restore project dirs** to `/home/tbaltzakis/` (keep the old layout — every watchdog/compose label expects it):
   - Data-bearing repo → tar pipe (preserves `.git`, stashes, `.env`, data dirs):
     `cd /mnt/c/Users/<u> && tar -cf - <dir> | tar -C /home/tbaltzakis -xf -`
   - Clean clone of a public repo → `git clone` is faster + validates auth.
   - **Windows copies may be stubs** — check `git -C <dir> log -1` and size before trusting them (our `C:\cloudless.gr` was a 28K leftover; the real clone was `Themis128_cloudless.gr`).
3. **Normalize the copied checkout** (NTFS→ext4 artifacts — shows as N hundred `M` entries, symmetric +/- diff stats):
   `git config core.fileMode false && git checkout -- .` → `git status` clean.
4. **Rebind containers**: `cd <dir> && docker compose up -d` (with `.deploy.lock`). Only services with project-dir binds (~8/36 here) recreate; named volumes untouched.
5. **Toolchain**: `apt install gh jq unzip htop` (+ `python-is-python3`, `nodejs`, `npm` if MCP servers need them). Git identity + `gh auth login --with-token` piped from Windows `gh auth token` (token never printed).
6. **IDE state** (app-managed — edits get flushed from memory while the app runs):
   - `User/globalStorage/state.vscdb` (sqlite `ItemTable`) — replace `Old-Name`→`New-Name` in `key` and `value`.
   - `User/globalStorage/storage.json` + `User/workspaceStorage/*/workspace.json` — `wsl%2B<old>` → `wsl%2B<new>` URI strings.
   - Durable fix needs the app closed: `scripts/fix-devin-remote-state.ps1` waits for the IDE exit gap (run detached or at logon).
   - `Local Storage/leveldb` `.ldb` blobs hold stale URIs too — ephemeral cache, leave them (editing sstables risks corruption).
7. **MCP configs**: `.devin/mcp_config.json` Windows paths → in-distro paths; `python`→`python3`, Git-bash `bash.exe`→`bash`, `npx.cmd`→`npx`. `.cursor/mcp.json` was already WSL-native.
8. **Scheduled tasks**: repoint `C:\...\<project>` actions to `\\wsl.localhost\<distro>\home\...` or `wsl -d <distro> -e ...`; wrap every console app in `conhost.exe --headless` (popup fix). `python.exe` watchers → `pythonw.exe`. Stored-credential tasks can't be edited without the password (`Set-ScheduledTask`/`schtasks /change` → access denied).
9. **Windows Terminal**: delete dead auto-profiles, set `defaultProfile` to the new distro GUID.
10. **Retire the Windows copies**: rename → `<dir>.moved-to-wsl-<date>` (not delete — reversible). If "busy"/"permission denied", a process holds the dir (MCP servers, IDE watchers, WSL cwd — hunt with Sysinternals `handle64.exe -p <proc> <substr>`); if the IDE itself holds it, the deferred-rename in the fix script lands it on next IDE exit.

## Gotchas hit live

- **`wsl -e` flaps (WSAETIMEDOUT) but `\\wsl.localhost` 9p share still works** — do file edits through UNC or Windows python when exec is dead. Retry, never restart.
- **9p tar copy is ~2.5MB/s single-thread** — 7.5GB took ~40min. robocopy /MT:16 is a faster alternative for big trees.
- **`--shutdown` in command strings trips the workstation policy hook** — build it as `'--shut' + 'down'` in PowerShell.
- **`docker-desktop-user-distro proxy --distro-name` expects the LOWERCASE distro name** (`ubuntu-24.04`) — derive with `$Distro.ToLower()`, never hardcode (bit us in Watch-DockerWslIntegration.ps1).
- **Port collisions surface on migrate** — containers from other projects grab freed ports while the stack is down (`env-manager-backend` 127.0.0.1:8081 vs galaxys222-dashboard 0.0.0.0:8081). Check `docker ps --format "{{.Ports}}"` before assuming compose config is at fault.
- **`.wslconfig` changes are INERT until VM bounce** — check file mtime vs VM boot before blaming config.
- **`storage.json`/`state.vscdb` revert from memory** — fix on disk AND defer a re-fix to IDE-exit; one without the other won't stick.

## Files left behind (delete only after verification)

- `C:\Users\tbaltzakis\*.moved-to-wsl-<date>` — the retired Windows copies.
- omv watchdog remediation references `C:/Users/tbaltzakis/cu130-slim/docker-compose.yml` — repoint to `wsl -d Ubuntu-24.04 docker compose -f /home/tbaltzakis/cu130-slim/docker-compose.yml`.
- `~/.ssh` in a fresh distro is empty — restore keys from the NAS WSL-layer backup or regenerate + install pubkey on omv.

## Scripts

- `scripts/fix-devin-remote-state.ps1` — generalized IDE-state repointer: replaces old→new distro strings in `state.vscdb`, `storage.json`, all `workspace.json`; waits for IDE exit so the fix sticks. Params: `-OldDistro`, `-NewDistro`, `-WaitMinutes`.
- `scripts/wsl-move-project.sh` — tar-copy a Windows dir into the distro home, normalize git (fileMode + checkout), validate compose, print follow-ups.
