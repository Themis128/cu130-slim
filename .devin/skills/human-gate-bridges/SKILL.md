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

## LinkedIn export click — `li_export_request.py`
Drives LinkedIn's data-privacy export page through the shared browser
bridge (`/session/evaluate`, same pattern as skool-ops). Requires an
active LinkedIn session on the bridge — session_healer covers it.

## X OAuth approve — `x_oauth_approve.py <url>`
Drives the X consent page through the bridge — paste the authorization
URL `wire.py` prints, the script clicks Authorize and prints back the
`?code=` redirect for the callback paste. Requires an X session on the
bridge.

## The residual human acts
1. None for LinkedIn export if the bridge session is live —
   `li_export_request.py` clicks for you
2. Same for X — `x_oauth_approve.py` approves for you
3. The SMS code — ADB reads it (wa_verify.sh) or type it manually
