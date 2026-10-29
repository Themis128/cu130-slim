# ntfy-phone-ops

Fix the ntfy Android app's server URL on the phone via ADB — for the class of
issue where the app can't resolve its configured host (e.g. a tailnet hostname
with no matching tailnet node → `UnknownHostException`, infinite retry loop).

## What it does

`scripts/ntfy_set_server.py <new-base-url>`:

1. Reads the phone's ntfy shared-prefs server config (via `run-as` backup or
   the UI dump) to confirm the current (broken) base URL.
2. Drives Settings → Default server to the new URL via uiautomator.
3. Re-adds the `cloudless-alerts` subscription via the `ntfy://` deep link
   (`am start -a VIEW -d ntfy://host/topic`) — the app prompts Subscribe.
4. Verifies with a screenshot check that the connection error cleared.

## When to use

- "ntfy keeps retrying / can't resolve host" on the phone
- Tailnet hostname in the app config that no longer exists (renamed node,
  dead MagicDNS entry)
- Any ntfy server-URL migration on the S22+

## Notes

- The ntfy app stores server config in shared_prefs — no direct file edit
  without root, so the script drives the settings UI like a user would.
- `StrictHostKeyChecking`-style trust: the script verifies the new base URL
  answers `GET /` before swapping it in — never points the app at a dead host.
- Requires `adb devices` showing the phone (wireless ADB via phone-access skill).
