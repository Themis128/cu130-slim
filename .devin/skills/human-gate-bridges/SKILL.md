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

## WhatsApp verify — `wa_verify.py`
Calls `request_code` (Graph API, SMS by default), then polls **three**
sources for the 6-digit code: the Galaxy S22+ SMS inbox via ADB
(`content://sms/inbox`), **Termux SSH** `termux-sms-list` over
`100.84.31.85` (tailnet; `192.168.1.77` also resolves via `~/.ssh/config` → port 8022, key `~/.ssh/id_ed25519_phone` — works when
network-ADB is dead; needs `READ_SMS` granted to Termux:API once
on-device), **and** the mailbox via doveadm (SMS→email forward covers
phone-off). Submits `verify_code`. Last resort: `wa_verify.py <code>` —
the rest is still scripted.

## LinkedIn export — `li_export_watch.py` + `li_export_download.py`
You click "Request data export" in LinkedIn's UI once (their portal, no
API). When the archive is ready, `li_export_download.py` fetches the ZIP
through the live **linkedin-browser-sidecar** session — the
`settingsApiArchivedMemberDataDownload` endpoint returns the archive
inline (JSESSIONID cookie as `csrf-token`); bytes are base64-chunked out
and `Connections.csv` lands in `Downloads/` for `audience_watch.py`.
The repo isn't mounted into social-api — stage it, run it, copy the CSV
back to Downloads:
```
docker cp .devin/skills/human-gate-bridges/scripts/li_export_download.py social-api:/tmp/
docker exec social-api python3 /tmp/li_export_download.py
docker cp social-api:/tmp/Connections.csv /mnt/c/Users/tbaltzakis/Downloads/
python3 .devin/skills/audience-watch-ops/scripts/audience_watch.py
```
The old `li_export_watch.py` mailbox lane still exists but the
export-ready email goes to the account's Gmail, not omv-ha — treat the
sidecar download as the working lane.

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
3. The SMS code — three lanes (ADB / Termux-SSH / mail-forward) read it;
   `wa_verify.py <code>` as last resort. One-time on-device: grant
   Termux:API `READ_SMS` (or plug USB / enable wireless debugging for ADB).
4. FB 2FA enroll — `meta_2fa_drive.py` automates the chain end-to-end
   (profile switch → password → Gmail code → otpauth secret → `.env`);
   inputs: live ADB + the real FB password (`--pw` or `FB_PASSWORD` env).

## TikTok QR login — `tiktok_qr_phone.py` + `scripts/tiktok_qr_watch.py`

When `tiktok_reauth.py` parks on the QR page in the headed bridge (:9223),
the QR encodes a login URL — no camera needed:

1. `tiktok_qr_phone.py` waits for the QR page, screenshots via
   `/session/screenshot`, decodes the payload (zxing-cpp → cv2 fallback),
   and fires `am start -a android.intent.action.VIEW -d <url>` on the
   phone (ADB `TK_ADB_SERIAL`, default 192.168.1.77:5555). The phone
   browser opens the login-confirm page — the human taps "Confirm" (or it
   completes silently if already authorized).
2. `tiktok_qr_watch.py` polls :9223 for real `sessionid`/`sid_tt` cookies
   (URL-change alone is NOT proof) and injects them into the :9224
   sidecar.

If the payload isn't an https URL the phone path can't open it — manual
scan remains the fallback (`--decode-only` reports the payload).

## Gmail OTP off the phone — `phone_gmail_otp.py`
When a verification code arrives by **email** (e.g. the Meta
"Unlock your Meta Account" device-verification lock — code mailed to
baltzakis.themis@gmail.com), the phone's own Gmail app is a readable
source with no root/OCR:

- `dumpsys notification --noredact` — Gmail post extras carry
  subject+snippet ("Use 16919622 to get back into...").
- `monkey -p com.google.android.gm` + `uiautomator dump` — inbox rows are
  text nodes containing the code.

`phone_gmail_otp.py [--sender meta] [--timeout S] [--open]` polls both
lanes, sender-scoped so other services' codes don't false-positive.
Env: `PHONE_ADB`, `PHONE_ADB_SERIAL`, `PHONE_OTP_SENDERS`. Prints the
code, exit 0/1. Same sender-whitelist discipline as `wa_verify.py`.
