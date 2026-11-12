---
name: fb-reauth-ops
description: Re-auth a dead Facebook/IG token — mints the FB OAuth consent URL
  (FACEBOOK_CLIENT_ID + redirect + scopes) and drives it on the bound debug
  Chrome (:9222) like the LinkedIn path. Tool: scripts/fb_reauth.py —
  --url prints the consent URL, no-args drives it on CDP. Fires after the
  CloudlessDebugChrome logon task binds :9222.
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
