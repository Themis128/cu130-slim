# Termux-first — always use Termux for phone-side work

Standing directive (2026-11-17): **for anything on the Galaxy S22+, reach for
Termux first.** It's the only phone app that accepts injected input —
`com.whatsapp.w4b` registration and other hardened apps filter synthetic
taps/text, but `com.termux` takes `input text`/`keyevent` fine and its
terminal scrollback is readable via uiautomator `content-desc`.

## What Termux gives us on-device

- `termux-api` 0.60.0: `termux-sms-list` (inbox JSON — OTP reads),
  `termux-clipboard-set` (paste-only human gates), `termux-notification-list`,
  `termux-toast`/`termux-dialog`/`termux-vibrate` (human signals).
- A real shell: `pkg install` works over tailnet; scripts can loop, poll,
  and write results to `/sdcard` for `adb pull`.
- `termux.boot` is installed → persistent on-device watchers survive reboot.
- `sshd` not yet installed — `pkg install openssh` + `authorized_keys`
  unlocks the `wa_verify.py` `:8022` lane and plain `ssh` driving.

## Order of operations for a phone task

1. Can Termux do it? (SMS/OTP read, clipboard, file staging, on-device
   polling, command exec) → `termux-ops` skill,
   `.devin/skills/termux-ops/scripts/termux_drive.py`
   (`open|run|push-run|screen|sms|clipboard|watch-sms`).
2. Needs an app UI that accepts injection (Termux, FB app) → adb
   `input`/`uiautomator` per `phone-ui-automation`.
3. App filters synthetic input (WhatsApp registration, passkey prompts) →
   shrink the human gate: clipboard-preload, on-screen toast, exact steps —
   never burn cycles on taps that can't land.

## Conventions

- `push-run` a script file for anything with quotes/`$`/`;` — `input text`
  mangles specials.
- Results via `/sdcard/*` + `adb pull`; `.exe` adb falls back through
  `mcp-hide` (no conhost flash — see `popup-free-host` rule).
- Grant API permissions via `pm grant com.termux.api <perm>` first.
- A running foreground loop blocks typed input — keep watchers bounded.
