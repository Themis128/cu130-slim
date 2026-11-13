# phone-2fa-ops — read 2FA codes off the phone, hands-free

Pulls Meta/WhatsApp (and any "Use NNNN for X") verification codes
straight from the phone's notification shade over ADB — no screen unlock,
no copy-paste. Proven 2026-10-29 pulling the live Meta code `820049` off
the S22+ notification dump and submitting it via CDP — logged the debug
browser in end-to-end.

## Tool — `scripts/fb_code.py`

```
python3 fb_code.py            # newest Meta/WA code from notifications
python3 fb_code.py --latest   # poll up to 120s for a fresh code
python3 fb_code.py --fill     # fill + submit the browser two_factor field via CDP
```

Env: `ADB`, `ADB_SERIAL` (default `192.168.1.77:5555`), `CDP_URL`
(default `http://100.83.120.93:9223`).

## How it works

- `dumpsys notification --noredact` — reads every live notification's
  `android.text`; matches `Use (\d{4,8}) for two-factor`. Works with the
  screen off — the shade is populated by the OS, not the UI.
- Auto-wakes the device first (WAKEUP + menu key) in case the shade
  needs a nudge.
- `--fill` drives the debug browser's `two_factor` tab over the CDP
  forwarder: React-native setter → input event → Continue click.
  `suppress_origin=True` (Chrome rejects non-localhost Origin).

## Pair it with

- `gate-runner-ops` — the clone→launch→watch→click runner
- `meta-totp-capture` — capture the authenticator secret so codes aren't
  needed at all next time
- `totp-ops` — the X-side equivalent

## Tool — `scripts/phone_fb_approve.py` (approve-from-another-device lane)

Meta sends a *push approval* ("Someone's trying to log in") alongside —
and sometimes INSTEAD of — a code prompt. This script taps "Yes, it's
me" on the phone so the sidecar's pending login completes server-side.

```
python3 phone_fb_approve.py --once   # one poll cycle (cron every 2min)
python3 phone_fb_approve.py --status # show state + recent results
```

Deployed copy: `~/scripts/phone_fb_approve.py` (home-ops repo;
`~/scripts` symlinks there). Cron: `*/2 * * * *`. State:
`~/.local/state/phone-fb-approve.json` (dedupes by approval_id). Log:
`~/logs/phone-fb-approve.log`.

### How it works

1. Dumps UIAutomator XML (`uiautomator dump`) — never OCR — and finds
   Facebook "trying to log in" notifications, extracting `approval_id`
   from the notification text/deep link.
2. Fires `fb://approve_from_another_device/open?approval_id=<id>&use_case=two_factor`
   at `com.facebook.katana`, up to 3 attempts with a HOME-press reset
   between them (the link is ~30% reliable at foregrounding the dialog).
3. Fallback: pulls the notification shade and taps the notification's
   bounds directly.
4. Verifies the dialog text, extracts the device string, and checks it
   against the allowlist (`linux`, `wsl`, `windows`, `chrome`, `edge`)
   — unknown devices are refused and fire a Telegram alert.
5. Taps only the positive control; text matching
   `cancel|decline|not now|maybe later|deny|wasn't me` is never clicked.

### Paired healer behaviour

`ig_fb_session_heal.py` calls `approval_wait(state, force=True)` when
2FA fires without an SMS code. This account shows a code-entry page
while Meta *also* pushes an approval — `force=True` skips the old
"waiting for approval" text gate and polls `/debug/eval` (never
`validate()`, which navigates and kills the pending flow) for up to
10 min until the URL leaves auth/checkpoint pages (`_AUTH_PAGE_RE`).

### Known gap

The `fb://` deep link does not always foreground the dialog. Next
upgrade: drive the FB app's internal Notifications tab (bottom-nav
tab 5) where the approval card has native Approve/Deny buttons.
