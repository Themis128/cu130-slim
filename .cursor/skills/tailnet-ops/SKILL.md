# tailnet-ops

Administer the office tailnet (tail4ecae1 — MagicDNS `*.tail4ecae1.ts.net`): ACL edits, serve config, device/tag inventory. Use for any Tailscale access-policy work — e.g. the `tag:authentik-admin` grant gating authentik admin to admins-only.

## Two automation paths (try in order)

1. **API path** — needs `TAILSCALE_API_KEY` (from admin console → Settings → Keys, or OAuth client). Store in `.env` or `~/.config/secrets/tailscale-api`. `scripts/apply_acl.py` reads it, merges tag+grant into the LIVE ACL JSON (never overwrite — fetch → merge → PUT).
2. **Playwright console path** — when no API key exists: drive `login.tailscale.com/admin/acls` with the playwright MCP (persistent profile keeps Google/Microsoft session). Steps in `scripts/console-acl.md` — opens editor, injects the tag+grant JSON via `browser_evaluate`, saves.

## The ACL block this stack wants

```json
"tagOwners": { "tag:authentik-admin": ["autogroup:admin"] },
"grants": [
  { "src": ["tag:authentik-admin"], "dst": ["omv", "omv-ha"], "ip": ["tcp:22", "tcp:443", "tcp:6443", "tcp:30910"] }
]
```

- `tag:authentik-admin` on devices that may administer authentik (office-1).
- Grant restricts SSH/admin/API to tagged devices (defense-in-depth on top of CF Access + tailscale serve).

## Gotchas hit live
- WSL `wsl -e` runs as root → ssh identity is `/root/.ssh`, NOT `/home/tbaltzakis/.ssh` — copy keys or use `-u tbaltzakis`.
- OMV `StrictModes` rejects home dirs with ANY path component writable (777 home-migration dir broke key auth).
- `tailscale serve --bg --set-path admin` worked for `/if/admin` proxying to NodePort.
- API key creation is console-only the FIRST time; afterwards everything is scriptable.

## Auto-update + health (always-perfect tailnet)
- `/home/tbaltzakis/tailnet-autoupdate.sh` — weekly run: `tailscale update` on every reachable node (omv, omv-ha), API audit (offline>1d, pending updates), tg_alert P2 on failures. Log: `~/.local/state/tailnet-autoupdate.log`.
- Windows task `TailnetAutoupdate` runs it weekly + updates office-1's client locally (elevated).
- Tag-locked nodes (kube-0, ingress-0, k3s-subnet-router-0, tailscale-operator, grokbot-box-1) aren't SSHable — flagged in the audit output, update manually or via k8s/node tooling.


## Phone (Android) posture — scripts/phone-audit.sh
Verifies the 3 on-device toggles the Tailscale API can't see, via ADB-over-tailscale
(`adb connect 100.84.31.85` — needs wireless debugging enabled once):
1. always-on VPN → `settings get secure always_on_vpn_app` = com.tailscale.ipn
2. battery Unrestricted → `dumpsys deviceidle` contains tailscale
3. Use Tailscale DNS → VPN DNS server = 100.100.100.100 in dumpsys connectivity
Tailnet-side confirmed/set remotely: client current, keyExpiryDisabled, authorized.


## Phone/Termux always-connected state (galaxy-s22)
ADB-applied + API-verified on 2026-11-04:
- Tailscale: always-on VPN, battery unrestricted, Tailscale DNS, keyExpiryDisabled — all set
- Termux: `dumpsys deviceidle whitelist +com.termux` (+ com.termux.boot) applied via ADB
- `adb -s 100.84.31.85:5555 shell ...` works — wireless debugging on port 5555
- Termux-side steps are on the phone (see phone-termux skill): wake-lock, sshd, ~/.termux/boot/start-sshd.sh
- Once termux sshd is up: `ssh -p 8022 galaxy-s22.tail4ecae1.ts.net` from any tailnet device


## termux-drive.sh — ADB UI-injection into Termux
Pushes the full setup (wakelock → openssh install → sshd → boot script) into a
focused Termux window over the tailnet. Usage: `termux-drive.sh [ip:port]`.
Requires the cover screen OPEN (CoverHomeActivity blocks input — exit 3 = ask
user to open the flip cover). `input text` needs spaces as %s; run-as is denied
on release builds so injection is the only no-root path.


## termux remote-exec — scripts/termux-{bootstrap,connect}.sh
- `termux-bootstrap.sh` — zero-to-SSH runbook as code: wake → launch → inject sshd → /sdcard keybridge → verify `ssh -p 8022`. Baked-in quirks: input-text mangles `~ && >` (absolute paths, one command per injection), CoverHomeActivity/systemui focus quirks (inject anyway, verify by :8022 LISTEN), run-as denied on release builds.
- `termux-connect.sh` — `termux-connect.sh [ip] cmd…` — ssh -p 8022 into the phone (key already installed).


## Unattended join + exit node — scripts/ts-authkey.sh, exit-node.sh
- `ts-authkey.sh [host|local]` — mints a preauthorized `tskey-auth-*` via `POST /tailnet/-/keys` (1h expiry), runs `tailscale up --auth-key` locally or over SSH — skips the browser-approval loop entirely. Use for any new node.
- `exit-node.sh <ssh-host>` — `tailscale set --advertise-exit-node` + `tailscale up`, waits 15s, approves the advertised `0.0.0.0/0 + ::/0` via API. tag:k8s nodes (omv-ha) are operator-managed — local `set` won't sync; use `github-omv` or another user-managed node.


## Theft resilience — phone-lockdown + phone-kill
- The phone shares your user identity — `*:*` gives a thief everything. The ACL now splits it: workstations keep `*:*` by IP, `galaxy-s22` (100.84.31.85) is scoped to service ports only (wsl ssh 22, omv web 443/30910, omv-ha 443) — no 22/6443/admin on servers.
- `phone-kill.sh` — instant revoke via `DELETE /device/{id}`. One command = phone's tailnet access dies immediately. Run it the moment the phone is lost; re-add later with `ts-authkey.sh`.


## Node map (2026-11-04)
| node | ip | role |
|---|---|---|
| office-1-win-exitnode | 100.83.120.93 | Windows workstation — exit node |
| wsl-ubuntu2404 | 100.86.188.43 | WSL2 dev env — sshd :22, /home/tbaltzakis share |
| galaxy-s22 | 100.84.31.85 | phone — Termux :8022, service-scoped ACL |
| github-omv | 100.74.191.58 | OMV NAS RPi — k3s+authentik+cloudflared+app-connector (DO NOT rename: admin URL bound to it) |
| omv-ha | 100.95.117.84 | OMV-HA box, tag:k8s operator-managed |
| tailscale-operator | 100.85.166.50 | k8s operator |
| k3s-subnet-router-0 | 100.93.92.77 | k8s subnet router pod |
| ingress-0 | 100.110.250.58 | k8s ingress pod |
| grokbot-box-1 | 100.65.100.39 | grokbot host (was offline ~1d) |
