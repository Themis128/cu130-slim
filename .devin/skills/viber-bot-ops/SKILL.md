---
name: viber-bot-ops
description: Provision the Viber bot — partners.viber.com is RETIRED (site
  dead — 'please refer to your app'). The App Token now comes from the Viber
  mobile app — your bot → Edit Info → the token field. Copy it, then
  viber_provision.py --token <token> --name cloudless does the rest
  unattended (connect → validate → webhook set). Tool: viber_token.py
  --check reports connection state; adb_sms.py reads OTP SMS off the
  ADB-connected phone for any phone-gated step.
---

# Viber bot provisioning

The web portal's gone — the token's in the app:

1. Viber app → your bot (Cloudless) → Edit Info → App Token → copy
2. `viber_provision.py --token <token> --name cloudless`

`adb_sms.py` reads phone-SMS codes via ADB for any OTP step. The portal drive
in `viber_token.py` is kept for the token-capture path if a new web surface
appears.
