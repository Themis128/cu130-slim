---
name: tailnet-watch-ops
description: Operate the office-1 tailnet-watch status check and its daily digest email — run the check on Windows from WSL, read the hourly history and digest state, force a digest send, and verify the scheduled task. Use for "tailnet-watch status" Telegram alerts, a missing/duplicate 08:00 digest at tbaltzakis@cloudless.gr, or after changing D:\tailnet-watch.
---

# tailnet-watch-ops

Read-only reporting layer of [Themis128/tailnet-watch](https://github.com/Themis128/tailnet-watch).
Nothing here touches ACLs, routes, DNS, exit nodes or device settings.

## Moving parts (office-1, Windows)

| Piece | Where |
|---|---|
| Checkout (master) | `D:\tailnet-watch` |
| Hourly task | `\DevOptimizer-Tailnet-Watch-Status` → `scripts\run-status-check.ps1` |
| Command | `tailnet-watch-status.py --tg --digest tbaltzakis@cloudless.gr` |
| Run log | `D:\tailnet-watch\logs\status.log` |
| Run history (7d) | `D:\tailnet-watch\logs\status-history.jsonl` |
| Last digest sent | `D:\tailnet-watch\logs\status-digest.json` |
| Mail relay creds | `%APPDATA%\devin\secrets\mail-outbound.json` (`url`, `token`) — never print |

Behaviour: every run records one history line; Telegram fires only on BAD;
the digest goes out on the first run at/after 08:00 local (`--digest-hour`),
once per day, and retries next hour if the send fails. The first digest
covers all retained history. Digest storage errors never block the
Telegram alert. `--mail <addr>` still emails the full report every run.

The script refuses to run outside Windows (exit 2) — WSL has no scheduled
task and different SSH keys, so it would print false BADs.

## Tool

`scripts/tw_status.sh` (run from WSL; uses `powershell.exe` interop):

```bash
.devin/skills/tailnet-watch-ops/scripts/tw_status.sh check     # live run, no TG/mail
.devin/skills/tailnet-watch-ops/scripts/tw_status.sh history   # last 24 history lines + digest state
.devin/skills/tailnet-watch-ops/scripts/tw_status.sh task      # scheduled task last/next run + result
.devin/skills/tailnet-watch-ops/scripts/tw_status.sh deploy    # git pull --ff-only in D:\tailnet-watch
```

To force a digest now: delete `status-digest.json`, then run the task
(`schtasks /run /tn \DevOptimizer-Tailnet-Watch-Status`) after 08:00.

## Triage

- **WARN grokbot offline** — expected between Grok sessions; not an outage.
- **WARN last watch.log line is an error** — usually a phone `adb … timed out`
  while the phone sleeps; see `phone-adb-ops`.
- **BAD phone full tunnel** — rerun once; a single miss is transient. If it
  repeats in the digest, see `phone-net-ops`.
- **`digest -> …: HTTP 403`** — the mail Worker needs the `/send` URL, an
  explicit `User-Agent`, and `category: "ops-alert"`.
- **No digest at 08:00** — `history` shows whether runs are being recorded;
  `task` shows whether the task ran. A send error is printed in `status.log`.

Related: `tailnet-ops` (ACL/devices), `tailscale-acl-ops`, `phone-net-ops`.
