---
name: credential-reset-ops
description: >
  Password-reset drive for accounts where the credential is lost —
  automates the "Forgot password" chain end-to-end: identify account,
  request code, self-read the code off the phone/Gmail (same lanes as
  wa_verify), set new password, store to .env/secret-store. Currently
  covers Facebook (sidecar). Tool: scripts/fb_pw_reset.py.
---

# Credential Reset Ops — automate "Forgot password"

The FB login credential isn't in any secret store (only OAuth tokens) —
when the password is lost, this drives the legitimate recovery flow.

## `fb_pw_reset.py <new-password>`

Chain (all via the facebook sidecar `:9226` + the phone code lanes):

1. `login/identify` → enter account id → Search/Continue
2. Request the code (`Send code`/`Continue`)
3. Poll the code — **3 lanes** (same as `wa_verify.py`):
   - ADB `content://sms/inbox` (Meta/AUTHMSG senders only)
   - Termux-SSH `termux-sms-list` @ `192.168.1.77:8022`
   - Gmail `security@facebookmail.com` via `gmail-token.json`
4. Enter code → set new password → `FB_PASSWORD` written to `.env`

`--dry` prints the chain without driving.

## Inputs

- Live sidecar session (logged-in FB) — `session_healer` keeps it
- Phone reachable on any code lane (USB-ADB, Termux, or the mail forward)
- The `gmail-token.json` secret for the email lane

## The honest gate

After reset, Meta may still gate the *next* sensitive change on
device-trust — the drive lands the password; the trust-aging is separate.
