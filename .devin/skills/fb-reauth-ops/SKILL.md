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
