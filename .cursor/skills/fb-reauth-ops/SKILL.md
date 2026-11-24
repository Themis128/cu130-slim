---
name: fb-reauth-ops
description: >-
  Re-auth a dead Facebook/IG token — mints the FB OAuth consent URL
  (FACEBOOK_CLIENT_ID + redirect + scopes) and drives it on the bound debug
  Chrome (:9222) like the LinkedIn path. Tool: scripts/fb_reauth.py —
  --url prints the consent URL, no-args drives it on CDP. Fires after the
  CloudlessDebugChrome logon task binds :9222. Includes
  fb_recover_drive.py, a passwordless login lane via the emailed recovery
  code.
---

# FB re-auth

`fb_reauth.py` → FB OAuth URL → CDP consent → fresh token. The dead-token
heal flag clears once consent lands.

## fb_session_heal.py — keep the debug-Chrome FB session alive

The OAuth-consent and Meta-Business lanes (fb_reauth, meta_billing_watch,
meta_payment_helper) all die when the chrome-debug-profile's facebook.com
session logs out. `scripts/fb_session_heal.py` closes that loop:

- `status` / `capture` / `heal` — probe → cookie re-inject (c_user+xs) →
  credential login via FACEBOOK_USERNAME/PASSWORD → checkpoint
  click-through → 2FA code pulled off the phone (phone-2fa-ops fb_code.py)
  → cookie jar re-stored. TG #ops alert on human-needed (deduped 6h).
- Reuses an existing facebook.com CDP tab when present — fresh FB tabs
  take ~60-90s before Runtime.evaluate responds on this RAM-tight host;
  the script polls for eval liveness instead of fixed sleeps.
- Cookie jar: ~/.local/state/fb-debug-session/ (outside repo, 0600).
- Cron: */30 `heal` (flock-guarded). Companion to ig_fb_session_heal.py
  which covers the facebook-browser-SIDECAR session — this one covers
  the :9229 debug Chrome used for OAuth/Business pages.

## fb_recover_drive.py — passwordless login via the emailed recovery code

When the session is dead AND the stored cookies + credential login both
fail (Meta anti-retry flags repeated password submits — do NOT retry the
password lane, use this instead):

- `drive` — full pass: /login/identify → "Get code via email" → poll the
  mailbox for the fresh 8-digit code (`ssh omv-ha doveadm`, newest uid) →
  submit → report end-state. `code <8digits>` re-submits a known code;
  `status` reports the current gate.
- End states: `waiting-device-approval` = a human must tap "Was this
  you?" on the trusted phone (Galaxy S22 FB app) — cannot be driven.
  `password-reset` = accepted; set the Meta Account password from the
  user's credential store. `logged-in` = run `fb_session_heal.py capture`
  to store the fresh jar.
- Live-verified 2026-11-24: the Meta Account password was accepted on
  the picker gate but the 2FA SPA rendered blank; the recovery lane
  bypassed it entirely and reached the device-approval gate in one pass.
- Mail facts: codes live on **omv-ha** (not omv); Meta RE-USES the same
  code across sends in one recovery session — a bounce to the picker
  means the submit failed, not that the code is stale; `SINCE 15min`
  doveadm search + grep `[0-9]{8}` on the newest uid gets it.
