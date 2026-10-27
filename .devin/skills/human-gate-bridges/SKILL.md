---
name: human-gate-bridges
description: >
  Bridge scripts for the three remaining human inputs on the survey +
  dev-app pipeline: X bearer re-auth (wire.py wrapped with env/secret-store
  creds — you only approve in the browser), WhatsApp phone verify
  (request_code → ADB SMS poll → verify_code — the code reads itself off
  the phone), and the LinkedIn export watch (polls the mailbox for the
  "export ready" email → downloads+extracts Connections.csv → drops it in
  Downloads where audience_watch attaches it). Tool: scripts/.
---

# Human-Gate Bridges — automate around the hands

Each remaining manual input gets a scripted surface that does everything
except the physical act only you can do:

## X re-auth — `x_reauth.sh`
Pulls `X_CLIENT_ID`/`X_CLIENT_SECRET` from env or the platform secret
store, runs `x-oauth-wire/wire.py`. You approve once in the browser —
credentials never need pasting.

## WhatsApp verify — `wa_verify.sh`
Calls `request_code` (Graph API, SMS by default), then polls the Galaxy
S22+ SMS inbox via `adb shell content query --uri content://sms/inbox`
for a 6-digit code and submits `verify_code`. If ADB is offline, run
`wa_verify.sh <code>` with the code manually — the rest is still scripted.

## LinkedIn export — `li_export_watch.py`
You click "Request data export" in LinkedIn's UI once (their portal, no
API). The watcher polls the mailbox (`doveadm`, omv-ha) for the
export-ready email, downloads the ZIP, extracts `Connections.csv` into
`Downloads/` — where `audience_watch.py` harvests and attaches it.
`--daemon` polls every 10 min until the email arrives.

## The residual human acts
1. Request the LinkedIn export (one click, once)
2. Approve X OAuth in a browser (one click, once)
3. Hold the phone for the SMS code (or ADB reads it)
