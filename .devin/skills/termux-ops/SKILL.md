---
name: termux-ops
description: Drive the Termux suite installed on the Galaxy S22+ (com.termux +
  .api/.boot/.widget/.window) from WSL over adb — the one phone app that
  accepts injected input, so it doubles as an on-device command shell:
  termux-api SMS/clipboard/notification reads, sdcard file lane, pkg installs,
  and scripted flows other apps reject. Use for phone-side OTP reads, putting
  text on the phone clipboard, running on-device commands, or any task that
  needs a Linux env on the phone.
---

# termux-ops

Termux is the **only injectable lane on the S22+**: apps like WhatsApp
Business filter synthetic input on registration screens, but
`com.termux/.app.TermuxActivity` accepts `input text` + `input keyevent`
fine. The terminal's full scrollback is one big `content-desc` on
`com.termux:id/terminal_view` in the uiautomator dump — always readable.

## Verified state (2026-11-17)

- Installed: `com.termux`, `com.termux.api` (0.60.0), `.boot`, `.widget`,
  `.window`. `sshd` NOT installed (port 8022 down).
- `termux-setup-storage` ran once → `/sdcard` writes work, adb can
  `pull`/`push` the files termux produces.
- `pm grant com.termux.api android.permission.READ_SMS` granted via adb —
  `termux-sms-list -l N` returns the inbox as JSON.
- `termux-clipboard-set <text>` sets the system clipboard — collapses
  phone-side human gates to *long-press → Paste*.
- `pkg install` works over the tailnet (`pkg install -y termux-api`).

## Tool: `scripts/termux_drive.py`

```bash
python3 termux_drive.py open                  # bring Termux to front
python3 termux_drive.py run "uptime"          # type+run a short command
python3 termux_drive.py push-run script.sh    # push+`sh` a script file (no escaping issues)
python3 termux_drive.py screen                # dump terminal text
python3 termux_drive.py sms -l 15             # inbox JSON via termux-sms-list
python3 termux_drive.py clipboard 6977777838  # set system clipboard
python3 termux_drive.py watch-sms 360         # termux-side poll -> /sdcard/sms-poll.json
```

Conventions: `push-run` is preferred for anything with quotes/`$`/`;` —
`input text` mangles special chars. Result files go to `/sdcard/` then
`adb pull`. `.exe` adb falls back through `mcp-hide` (`_a()`) — no conhost
flash. A running foreground loop (`watch-sms`) blocks typed commands until
it exits — keep watchers short or put them in their own session.

## Useful termux-api commands

| Command | Use |
|---|---|
| `termux-sms-list -l N` | inbox JSON (OTP read lane #2 next to `content://sms/inbox`) |
| `termux-clipboard-set <t>` | preload fields for paste-only human gates |
| `termux-notification-list` | notification payloads (OTP from push) |
| `termux-toast <t>` | on-screen signal to the human |
| `termux-dialog` | prompt the human in-app |
| `termux-vibrate` | attention signal |

## Not yet built (add when needed)

- `sshd` (`pkg install openssh` + `authorized_keys` via push-run) → unlocks
  the existing `wa_verify.py` `:8022` lane and makes `ssh`-based driving
  possible without input injection.
- `com.termux.boot` is installed — a `~/.termux/boot/` script survives
  reboots (persistent on-device watchers).
- `RUN_COMMAND` intent (`allow-external-apps=true` in
  `~/.termux/termux.properties`) → broadcast-driven exec without focus.
