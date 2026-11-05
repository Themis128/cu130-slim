# WSL Git Sync

Reliable git push/pull for repos inside WSL2 `Ubuntu-24.04` when `wsl -e`/vsock flaps (`Wsl/Service/WSAETIMEDOUT`). Use when git ops inside WSL hang or exit 0 with no remote change — **verify with `git ls-remote`, never trust exit code**.

## Escalation ladder (proven 2026-11-04)
1. `scripts/retry-push.sh` — in-distro `timeout 60 git push` x5, 15s backoff, ls-remote verify.
2. Windows-git fallback: `scripts/push-via-windows.ps1 -Repo <wsl-path> -Branch <b>` — tars repo through `/mnt/c`, pushes via Windows git.exe + `gh auth setup-git`.
3. Nuclear: `git bundle` commits out, fetch+push from a Windows clone.
