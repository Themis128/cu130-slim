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
